from django.shortcuts import render_to_response, get_object_or_404
from django.http import HttpResponseRedirect, Http404, HttpResponseForbidden
from django.template import RequestContext
from django.db import transaction, connection
from django.db.models import Q
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User

from django.conf import settings

from datetime import datetime
from email.mime.text import MIMEText
from email.utils import formatdate, make_msgid, formataddr
from email.header import Header

from pgcommitfest.mailqueue.util import send_mail, send_simple_mail
from pgcommitfest.userprofile.util import UserWrapper

from models import CommitFest, Patch, PatchOnCommitFest, PatchHistory, Committer
from forms import PatchForm, NewPatchForm, CommentForm, CommitFestFilterForm
from forms import BulkEmailForm
from ajax import doAttachThread
from feeds import ActivityFeed

def home(request):
	commitfests = list(CommitFest.objects.all())
	opencf = next((c for c in commitfests if c.status == CommitFest.STATUS_OPEN), None)
	inprogresscf = next((c for c in commitfests if c.status == CommitFest.STATUS_INPROGRESS), None)

	return render_to_response('home.html', {
		'commitfests': commitfests,
		'opencf': opencf,
		'inprogresscf': inprogresscf,
		'title': 'Commitfests',
		'header_activity': 'Activity log',
		'header_activity_link': '/activity/',
		}, context_instance=RequestContext(request))


def activity(request, cfid=None, rss=None):
	# Number of notes to fetch
	if rss:
		num = 50
	else:
		num = 100

	if cfid:
		cf = get_object_or_404(CommitFest, pk=cfid)

		# Yes, we do string concatenation of the were clause. Because
		# we're evil.  And also because the number has been verified
		# when looking up the cf itself, so nothing can be injected
		# there.
		extrafields = ''
		where = 'WHERE poc.commitfest_id={0}'.format(cf.id)
	else:
		cf = None
		extrafields = ',poc.commitfest_id AS cfid,cf.name AS cfname'
		where = ' INNER JOIN commitfest_commitfest cf ON cf.id=poc.commitfest_id'

	sql = "SELECT ph.date, auth_user.username AS by, ph.what, p.id AS patchid, p.name{0} FROM commitfest_patchhistory ph INNER JOIN commitfest_patch p ON ph.patch_id=p.id INNER JOIN auth_user on auth_user.id=ph.by_id INNER JOIN commitfest_patchoncommitfest poc ON poc.patch_id=p.id {1} ORDER BY ph.date DESC LIMIT {2}".format(extrafields,where, num)

	curs = connection.cursor()
	curs.execute(sql)
	activity = [dict(zip([c[0] for c in curs.description],r)) for r in curs.fetchall()]

	if rss:
		# Return RSS feed with these objects
		return ActivityFeed(activity, cf)(request)
	else:
		# Return regular webpage
		return render_to_response('activity.html', {
			'commitfest': cf,
			'activity': activity,
			'title': cf and 'Commitfest activity' or 'Global Commitfest activity',
			'rss_alternate': cf and '/{0}/activity.rss/'.format(cf.id) or '/activity.rss/',
			'rss_alternate_title': 'PostgreSQL Commitfest Activity Log',
			'breadcrumbs': cf and [{'title': cf.title, 'href': '/%s/' % cf.pk},] or None,
		}, context_instance=RequestContext(request))

def redir(request, what):
	if what == 'open':
		cfs = list(CommitFest.objects.filter(status=CommitFest.STATUS_OPEN))
	elif what == 'inprogress':
		cfs = list(CommitFest.objects.filter(status=CommitFest.STATUS_INPROGRESS))
	else:
		raise Http404()

	if len(cfs) == 0:
		messages.warning(request, "No {0} commitfests exist, redirecting to startpage.".format(what))
		return HttpResponseRedirect("/")
	if len(cfs) != 1:
		messages.warning(request, "More than one {0} commitfest exists, redirecting to startpage instead.".format(what))
		return HttpResponseRedirect("/")

	return HttpResponseRedirect("/%s/" % cfs[0].id)

def commitfest(request, cfid):
	# Find ourselves
	cf = get_object_or_404(CommitFest, pk=cfid)

	# Build a dynamic filter based on the filtering options entered
	q = Q()
	if request.GET.has_key('status') and request.GET['status'] != "-1":
		q = q & Q(patchoncommitfest__status=int(request.GET['status']))
	if request.GET.has_key('author') and request.GET['author'] != "-1":
		if request.GET['author'] == '-2':
			q = q & Q(authors=None)
		elif request.GET['author'] == '-3':
			# Checking for "yourself" requires the user to be logged in!
			if not request.user.is_authenticated():
				return HttpResponseRedirect('%s?next=%s' % (settings.LOGIN_URL, request.path))
			q = q & Q(authors=request.user)
		else:
			q = q & Q(authors__id=int(request.GET['author']))
	if request.GET.has_key('reviewer') and request.GET['reviewer'] != "-1":
		if request.GET['reviewer'] == '-2':
			q = q & Q(reviewers=None)
		elif request.GET['reviewer'] == '-3':
			# Checking for "yourself" requires the user to be logged in!
			if not request.user.is_authenticated():
				return HttpResponseRedirect('%s?next=%s' % (settings.LOGIN_URL, request.path))
			q = q & Q(reviewers=request.user)
		else:
			q = q & Q(reviewers__id=int(request.GET['reviewer']))

	if request.GET.has_key('text') and request.GET['text'] != '':
		q = q & Q(name__icontains=request.GET['text'])

	has_filter = len(q.children) > 0

	# Figure out custom ordering
	ordering = ['-is_open', 'topic__topic', 'created',]
	if request.GET.has_key('sortkey') and request.GET['sortkey']!='':
		sortkey=int(request.GET['sortkey'])

		if sortkey==1:
			ordering = ['-is_open', 'modified', 'created',]
		elif sortkey==2:
			ordering = ['-is_open', 'lastmail', 'created',]
		else:
			sortkey=0
	else:
		sortkey = 0

	if not has_filter and sortkey==0 and request.GET:
		# Redirect to get rid of the ugly url
		return HttpResponseRedirect('/%s/' % cf.id)

	patches = list(cf.patch_set.filter(q).select_related().extra(select={
		'status':'commitfest_patchoncommitfest.status',
		'author_names':"SELECT string_agg(first_name || ' ' || last_name || ' (' || username || ')', ', ') FROM auth_user INNER JOIN commitfest_patch_authors cpa ON cpa.user_id=auth_user.id WHERE cpa.patch_id=commitfest_patch.id",
		'reviewer_names':"SELECT string_agg(first_name || ' ' || last_name || ' (' || username || ')', ', ') FROM auth_user INNER JOIN commitfest_patch_reviewers cpr ON cpr.user_id=auth_user.id WHERE cpr.patch_id=commitfest_patch.id",
		'is_open':'commitfest_patchoncommitfest.status IN (%s)' % ','.join([str(x) for x in PatchOnCommitFest.OPEN_STATUSES]),
	}).order_by(*ordering))

	# Generate patch status summary.
	curs = connection.cursor()
	curs.execute("SELECT ps.status, ps.statusstring, count(*) FROM commitfest_patchoncommitfest poc INNER JOIN commitfest_patchstatus ps ON ps.status=poc.status WHERE commitfest_id=%(id)s GROUP BY ps.status ORDER BY ps.sortkey", {
		'id': cf.id,
		})
	statussummary = curs.fetchall()
	statussummary.append([-1, 'Total', sum((r[2] for r in statussummary))])

	# Generates a fairly expensive query, which we shouldn't do unless
	# the user is logged in. XXX: Figure out how to avoid doing that..
	form = CommitFestFilterForm(cf, request.GET)

	return render_to_response('commitfest.html', {
		'cf': cf,
		'form': form,
		'patches': patches,
		'statussummary': statussummary,
		'has_filter': has_filter,
		'title': cf.title,
		'grouping': sortkey==0,
		'sortkey': sortkey,
		'openpatchids': [p.id for p in patches if p.is_open],
		'header_activity': 'Activity log',
		'header_activity_link': 'activity/',
		}, context_instance=RequestContext(request))

def global_search(request):
	if not request.GET.has_key('searchterm'):
		return HttpResponseRedirect('/')
	searchterm = request.GET['searchterm']

	patches = Patch.objects.select_related().filter(name__icontains=searchterm).order_by('created',)

	return render_to_response('patchsearch.html', {
		'patches': patches,
		'title': 'Patch search results',
		}, context_instance=RequestContext(request))

def patch(request, cfid, patchid):
	cf = get_object_or_404(CommitFest, pk=cfid)
	patch = get_object_or_404(Patch.objects.select_related(), pk=patchid, commitfests=cf)
	patch_commitfests = PatchOnCommitFest.objects.select_related('commitfest').filter(patch=patch).order_by('-commitfest__startdate')
	committers = Committer.objects.filter(active=True).order_by('user__last_name', 'user__first_name')

	#XXX: this creates a session, so find a smarter way. Probably handle
	#it in the callback and just ask the user then?
	if request.user.is_authenticated():
		committer = [c for c in committers if c.user==request.user]
		if len(committer) > 0:
			is_committer=  True
			is_this_committer = committer[0] == patch.committer
		else:
			is_committer = is_this_committer = False

		is_reviewer = request.user in patch.reviewers.all()
		is_subscribed = patch.subscribers.filter(id=request.user.id).exists()
	else:
		is_committer = False
		is_this_committer = False
		is_reviewer = False
		is_subscribed = False

	return render_to_response('patch.html', {
		'cf': cf,
		'patch': patch,
		'patch_commitfests': patch_commitfests,
		'is_committer': is_committer,
		'is_this_committer': is_this_committer,
		'is_reviewer': is_reviewer,
		'is_subscribed': is_subscribed,
		'committers': committers,
		'attachnow': request.GET.has_key('attachthreadnow'),
		'title': patch.name,
		'breadcrumbs': [{'title': cf.title, 'href': '/%s/' % cf.pk},],
		}, context_instance=RequestContext(request))

@login_required
@transaction.atomic
def patchform(request, cfid, patchid):
	cf = get_object_or_404(CommitFest, pk=cfid)
	patch = get_object_or_404(Patch, pk=patchid, commitfests=cf)

	prevreviewers = list(patch.reviewers.all())
	prevauthors = list(patch.authors.all())
	prevcommitter = patch.committer

	if request.method == 'POST':
		form = PatchForm(data=request.POST, instance=patch)
		if form.is_valid():
			# Some fields need to be set when creating a new one
			r = form.save(commit=False)
			# Fill out any locked fields here

			form.save_m2m()

			# Track all changes
			for field, values in r.diff.items():
				PatchHistory(patch=patch, by=request.user, what='Changed %s to %s' % (field, values[1])).save_and_notify(prevcommitter=prevcommitter, prevreviewers=prevreviewers, prevauthors=prevauthors)
			r.set_modified()
			r.save()
			return HttpResponseRedirect('../../%s/' % r.pk)
		# Else fall through and render the page again
	else:
		form = PatchForm(instance=patch)

	return render_to_response('base_form.html', {
		'cf': cf,
		'form': form,
		'patch': patch,
		'title': 'Edit patch',
		'breadcrumbs': [{'title': cf.title, 'href': '/%s/' % cf.pk},
						{'title': 'View patch', 'href': '/%s/%s/' % (cf.pk, patch.pk)}],
	}, context_instance=RequestContext(request))

@login_required
@transaction.atomic
def newpatch(request, cfid):
	cf = get_object_or_404(CommitFest, pk=cfid)
	if not cf.status == CommitFest.STATUS_OPEN and not request.user.is_staff:
		raise Http404("This commitfest is not open!")

	if request.method == 'POST':
		form = NewPatchForm(data=request.POST)
		if form.is_valid():
			patch = Patch(name=form.cleaned_data['name'],
						  topic=form.cleaned_data['topic'])
			patch.set_modified()
			patch.save()
			poc = PatchOnCommitFest(patch=patch, commitfest=cf, enterdate=datetime.now())
			poc.save()
			PatchHistory(patch=patch, by=request.user, what='Created patch record').save()
			# Now add the thread
			try:
				doAttachThread(cf, patch, form.cleaned_data['threadmsgid'], request.user)
				return HttpResponseRedirect("/%s/%s/edit/" % (cf.id, patch.id))
			except Http404:
				# Thread not found!
				# This is a horrible breakage of API layers
				form._errors['threadmsgid'] = form.error_class(('Selected thread did not exist in the archives',))
			except Exception:
				form._errors['threadmsgid'] = form.error_class(('An error occurred looking up the thread in the archives.',))
			# In this case, we have created a patch - delete it. This causes a agp in id's, but it should
			# not happen very often. If we successfully attached to it, we will have already returned.
			patch.delete()
	else:
		form = NewPatchForm()

	return render_to_response('base_form.html', {
		'form': form,
		'title': 'New patch',
		'breadcrumbs': [{'title': cf.title, 'href': '/%s/' % cf.pk},],
		'savebutton': 'Create patch',
		'threadbrowse': True,
	}, context_instance=RequestContext(request))

def _review_status_string(reviewstatus):
	if '0' in reviewstatus:
		if '1' in reviewstatus:
			return "tested, passed"
		else:
			return "tested, failed"
	else:
		return "not tested"

def _user_email_header(user):
	return formataddr((
		str(Header(u"%s %s" % (user.first_name, user.last_name), 'utf-8')),
		UserWrapper(user).email))

@login_required
@transaction.atomic
def comment(request, cfid, patchid, what):
	cf = get_object_or_404(CommitFest, pk=cfid)
	patch = get_object_or_404(Patch, pk=patchid)
	poc = get_object_or_404(PatchOnCommitFest, patch=patch, commitfest=cf)
	is_review = (what=='review')

	if poc.is_closed:
		# We allow modification of patches in closed CFs *only* if it's the
		# last CF that the patch is part of. If it's part of another CF, that
		# is later than this one, tell the user to go there instead.
		lastcf = PatchOnCommitFest.objects.filter(patch=patch).order_by('-commitfest__startdate')[0]
		if poc != lastcf:
			messages.add_message(request, messages.INFO, "The status of this patch cannot be changed in this commitfest. You must modify it in the one where it's open!")
			return HttpResponseRedirect('..')

	if request.method == 'POST':
		try:
			form = CommentForm(patch, poc, is_review, data=request.POST)
		except Exception, e:
			messages.add_message(request, messages.ERROR, "Failed to build list of response options from the archives: %s" % e)
			return HttpResponseRedirect('/%s/%s/' % (cf.id, patch.id))

		if form.is_valid():
			if is_review:
				txt = "The following review has been posted through the commitfest application:\n%s\n\n%s" % (
					"\n".join(["%-25s %s" % (f.label + ':', _review_status_string(form.cleaned_data[fn])) for (fn, f) in form.fields.items() if fn.startswith('review_')]),
					form.cleaned_data['message']
				)
			else:
				txt = form.cleaned_data['message']

			if int(form.cleaned_data['newstatus']) != poc.status:
				poc.status = int(form.cleaned_data['newstatus'])
				poc.save()
				PatchHistory(patch=poc.patch, by=request.user, what='New status: %s' % poc.statusstring).save_and_notify()
				txt += "\n\nThe new status of this patch is: %s\n" % poc.statusstring

			msg = MIMEText(txt, _charset='utf-8')

			if form.thread.subject.startswith('Re:'):
				msg['Subject'] = form.thread.subject
			else:
				msg['Subject'] = 'Re: %s' % form.thread.subject

			msg['To'] = settings.HACKERS_EMAIL
			msg['From'] = _user_email_header(request.user)

			# CC the authors of a patch, if there are any
			authors = list(patch.authors.all())
			if len(authors):
				msg['Cc'] = ", ".join([_user_email_header(a) for a in authors])

			msg['Date'] = formatdate(localtime=True)
			msg['User-Agent'] = 'pgcommitfest'
			msg['X-cfsender'] = request.user.username
			msg['In-Reply-To'] = '<%s>' % form.respid
			# We just add the "top" messageid and the one we're responding to.
			# This along with in-reply-to should indicate clearly enough where
			# in the thread the message belongs.
			msg['References'] = '<%s> <%s>' % (form.thread.messageid, form.respid)
			msg['Message-ID'] = make_msgid('pgcf')

			send_mail(UserWrapper(request.user).email, settings.HACKERS_EMAIL, msg.as_string())

			PatchHistory(patch=patch, by=request.user, what='Posted %s with messageid %s' % (what, msg['Message-ID'])).save()

			messages.add_message(request, messages.INFO, "Your email has been queued for pgsql-hackers, and will be sent within a few minutes.")

			return HttpResponseRedirect('/%s/%s/' % (cf.id, patch.id))
	else:
		try:
			form = CommentForm(patch, poc, is_review)
		except Exception, e:
			messages.add_message(request, messages.ERROR, "Failed to build list of response options from the archives: %s" % e)
			return HttpResponseRedirect('/%s/%s/' % (cf.id, patch.id))

	return render_to_response('base_form.html', {
		'cf': cf,
		'form': form,
		'patch': patch,
		'extraformclass': 'patchcommentform',
		'breadcrumbs': [{'title': cf.title, 'href': '/%s/' % cf.pk},
						{'title': 'View patch', 'href': '/%s/%s/' % (cf.pk, patch.pk)}],
		'title': "Add %s" % what,
		'note': '<b>Note!</b> This form will generate an email to the public mailinglist <i>%s</i>, with sender set to <i>%s</i>!' % (settings.HACKERS_EMAIL, UserWrapper(request.user).email),
		'savebutton': 'Send %s' % what,
	}, context_instance=RequestContext(request))

@login_required
@transaction.atomic
def status(request, cfid, patchid, status):
	poc = get_object_or_404(PatchOnCommitFest.objects.select_related(), commitfest__id=cfid, patch__id=patchid)

	if poc.is_closed:
		# We allow modification of patches in closed CFs *only* if it's the
		# last CF that the patch is part of. If it's part of another CF, that
		# is later than this one, tell the user to go there instead.
		lastcf = PatchOnCommitFest.objects.filter(patch__id=patchid).order_by('-commitfest__startdate')[0]
		if poc != lastcf:
			messages.add_message(request, messages.INFO, "The status of this patch cannot be changed in this commitfest. You must modify it in the one where it's open!")
			return HttpResponseRedirect('/%s/%s/' % (poc.commitfest.id, poc.patch.id))

	if status == 'review':
		newstatus = PatchOnCommitFest.STATUS_REVIEW
	elif status == 'author':
		newstatus = PatchOnCommitFest.STATUS_AUTHOR
	elif status == 'committer':
		newstatus = PatchOnCommitFest.STATUS_COMMITTER
	else:
		raise Exception("Can't happen")

	if newstatus != poc.status:
		# Only save it if something actually changed
		poc.status = newstatus
		poc.patch.set_modified()
		poc.patch.save()
		poc.save()

		PatchHistory(patch=poc.patch, by=request.user, what='New status: %s' % poc.statusstring).save_and_notify()

	return HttpResponseRedirect('/%s/%s/' % (poc.commitfest.id, poc.patch.id))


@login_required
@transaction.atomic
def close(request, cfid, patchid, status):
	poc = get_object_or_404(PatchOnCommitFest.objects.select_related(), commitfest__id=cfid, patch__id=patchid)

	if poc.is_closed:
		# We allow modification of patches in closed CFs *only* if it's the
		# last CF that the patch is part of. If it's part of another CF, that
		# is later than this one, tell the user to go there instead.
		lastcf = PatchOnCommitFest.objects.filter(patch__id=patchid).order_by('-commitfest__startdate')[0]
		if poc != lastcf:
			messages.add_message(request, messages.INFO, "The status of this patch cannot be changed in this commitfest. You must modify it in the one where it's open!")
			return HttpResponseRedirect('/%s/%s/' % (poc.commitfest.id, poc.patch.id))

	poc.leavedate = datetime.now()

	# We know the status can't be one of the ones below, since we
	# have checked that we're not closed yet. Therefor, we don't
	# need to check if the individual status has changed.
	if status == 'reject':
		poc.status = PatchOnCommitFest.STATUS_REJECTED
	elif status == 'feedback':
		poc.status = PatchOnCommitFest.STATUS_RETURNED
	elif status == 'next':
		# Only some patch statuses can actually be moved.
		if poc.status in (PatchOnCommitFest.STATUS_AUTHOR,
						  PatchOnCommitFest.STATUS_COMMITTED,
						  PatchOnCommitFest.STATUS_NEXT,
						  PatchOnCommitFest.STATUS_RETURNED,
						  PatchOnCommitFest.STATUS_REJECTED):
			# Can't be moved!
			messages.error(request, "A patch in status {0} cannot be moved to next commitfest.".format(poc.statusstring))
			return HttpResponseRedirect('/%s/%s/' % (poc.commitfest.id, poc.patch.id))
		elif poc.status in (PatchOnCommitFest.STATUS_REVIEW,
							PatchOnCommitFest.STATUS_COMMITTER):
			# This one can be moved
			pass
		else:
			messages.error(request, "Invalid existing patch status")

		oldstatus = poc.status

		poc.status = PatchOnCommitFest.STATUS_NEXT
		# Figure out the commitfest to actually put it on
		newcf = CommitFest.objects.filter(status=CommitFest.STATUS_OPEN)
		if len(newcf) == 0:
			# Ok, there is no open CF at all. Let's see if there is a
			# future one.
			newcf = CommitFest.objects.filter(status=CommitFest.STATUS_FUTURE)
			if len(newcf) == 0:
				messages.error(request,"No open and no future commitfest exists!")
				return HttpResponseRedirect('/%s/%s/' % (poc.commitfest.id, poc.patch.id))
			elif len(newcf) != 1:
				messages.error(request, "No open and multiple future commitfests exist!")
				return HttpResponseRedirect('/%s/%s/' % (poc.commitfest.id, poc.patch.id))
		elif len(newcf) != 1:
			messages.error(request, "Multiple open commitfests exists!")
			return HttpResponseRedirect('/%s/%s/' % (poc.commitfest.id, poc.patch.id))
		elif newcf[0] == poc.commitfest:
			# The current open CF is the same one that we are already on.
			# In this case, try to see if there is a future CF we can
			# move it to.
			newcf = CommitFest.objects.filter(status=CommitFest.STATUS_FUTURE)
			if len(newcf) == 0:
				messages.error(request, "Cannot move patch to the same commitfest, and no future commitfests exist!")
				return HttpResponseRedirect('/%s/%s/' % (poc.commitfest.id, poc.patch.id))
			elif len(newcf) != 1:
				messages.error(request, "Cannot move patch to the same commitfest, and multiple future commitfests exist!")
				return HttpResponseRedirect('/%s/%s/' % (poc.commitfest.id, poc.patch.id))
		# Create a mapping to the new commitfest that we are bouncing
		# this patch to.
		newpoc = PatchOnCommitFest(patch=poc.patch,
								   commitfest=newcf[0],
								   status=oldstatus,
								   enterdate=datetime.now())
		newpoc.save()
	elif status == 'committed':
		committer = get_object_or_404(Committer, user__username=request.GET['c'])
		if committer != poc.patch.committer:
			# Committer changed!
			prevcommitter = poc.patch.committer
			poc.patch.committer = committer
			PatchHistory(patch=poc.patch, by=request.user, what='Changed committer to %s' % committer).save_and_notify(prevcommitter=prevcommitter)
		poc.status = PatchOnCommitFest.STATUS_COMMITTED
	else:
		raise Exception("Can't happen")

	poc.patch.set_modified()
	poc.patch.save()
	poc.save()

	PatchHistory(patch=poc.patch, by=request.user, what='Closed in commitfest %s with status: %s' % (poc.commitfest, poc.statusstring)).save_and_notify()

	return HttpResponseRedirect('/%s/%s/' % (poc.commitfest.id, poc.patch.id))

@login_required
@transaction.atomic
def reviewer(request, cfid, patchid, status):
	get_object_or_404(CommitFest, pk=cfid)
	patch = get_object_or_404(Patch, pk=patchid)

	is_reviewer = request.user in patch.reviewers.all()

	if status=='become' and not is_reviewer:
		patch.reviewers.add(request.user)
		patch.set_modified()
		PatchHistory(patch=patch, by=request.user, what='Added %s as reviewer' % request.user.username).save_and_notify()
	elif status=='remove' and is_reviewer:
		patch.reviewers.remove(request.user)
		patch.set_modified()
		PatchHistory(patch=patch, by=request.user, what='Removed %s from reviewers' % request.user.username).save_and_notify()
	return HttpResponseRedirect('../../')

@login_required
@transaction.atomic
def committer(request, cfid, patchid, status):
	get_object_or_404(CommitFest, pk=cfid)
	patch = get_object_or_404(Patch, pk=patchid)

	committer = list(Committer.objects.filter(user=request.user, active=True))
	if len(committer) == 0:
		return HttpResponseForbidden('Only committers can do that!')
	committer = committer[0]

	is_committer = committer == patch.committer

	prevcommitter = patch.committer
	if status=='become' and not is_committer:
		patch.committer = committer
		patch.set_modified()
		PatchHistory(patch=patch, by=request.user, what='Added %s as committer' % request.user.username).save_and_notify(prevcommitter=prevcommitter)
	elif status=='remove' and is_committer:
		patch.committer = None
		patch.set_modified()
		PatchHistory(patch=patch, by=request.user, what='Removed %s from committers' % request.user.username).save_and_notify(prevcommitter=prevcommitter)
	patch.save()
	return HttpResponseRedirect('../../')

@login_required
@transaction.atomic
def subscribe(request, cfid, patchid, sub):
	get_object_or_404(CommitFest, pk=cfid)
	patch = get_object_or_404(Patch, pk=patchid)

	if sub == 'un':
		patch.subscribers.remove(request.user)
		messages.info(request, "You have been unsubscribed from updates on this patch")
	else:
		patch.subscribers.add(request.user)
		messages.info(request, "You have been subscribed to updates on this patch")
	patch.save()
	return HttpResponseRedirect("../")

@login_required
@transaction.atomic
def send_email(request, cfid):
	cf = get_object_or_404(CommitFest, pk=cfid)
	if not request.user.is_staff:
		raise Http404("Only CF managers can do that.")

	if request.method == 'POST':
		authoridstring = request.POST['authors']
		revieweridstring = request.POST['reviewers']
		form = BulkEmailForm(data=request.POST)
		if form.is_valid():
			q = Q()
			if authoridstring:
				q = q | Q(patch_author__in=[int(x) for x in authoridstring.split(',')])
			if revieweridstring:
				q = q | Q(patch_reviewer__in=[int(x) for x in revieweridstring.split(',')])

			recipients = User.objects.filter(q).distinct()

			for r in recipients:
				send_simple_mail(UserWrapper(request.user).email, r.email, form.cleaned_data['subject'], form.cleaned_data['body'], request.user.username)
				messages.add_message(request, messages.INFO, "Sent email to %s" % r.email)
			return HttpResponseRedirect('..')
	else:
		authoridstring = request.GET.get('authors', None)
		revieweridstring = request.GET.get('reviewers', None)
		form = BulkEmailForm(initial={'authors': authoridstring, 'reviewers': revieweridstring})

	if authoridstring:
		authors = list(User.objects.filter(patch_author__in=[int(x) for x in authoridstring.split(',')]).distinct())
	else:
		authors = []
	if revieweridstring:
		reviewers = list(User.objects.filter(patch_reviewer__in=[int(x) for x in revieweridstring.split(',')]).distinct())
	else:
		reviewers = []

	if len(authors)==0 and len(reviewers)==0:
		messages.add_message(request, messages.WARNING, "No recipients specified, cannot send email")
		return HttpResponseRedirect('..')

	messages.add_message(request, messages.INFO, "Email will be sent from: %s" % UserWrapper(request.user).email)
	def _user_and_mail(u):
		return "%s %s (%s)" % (u.first_name, u.last_name, u.email)

	if len(authors):
		messages.add_message(request, messages.INFO, "The email will be sent to the following authors: %s" % ", ".join([_user_and_mail(u) for u in authors]))
	if len(reviewers):
		messages.add_message(request, messages.INFO, "The email will be sent to the following reviewers: %s" % ", ".join([_user_and_mail(u) for u in reviewers]))

	return render_to_response('base_form.html', {
		'cf': cf,
		'form': form,
		'title': 'Send email',
		'breadcrumbs': [{'title': cf.title, 'href': '/%s/' % cf.pk},],
		'savebutton': 'Send email',
	}, context_instance=RequestContext(request))
