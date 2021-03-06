# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import migrations, models
from django.conf import settings
import pgcommitfest.commitfest.util


class Migration(migrations.Migration):

    dependencies = [
        ('auth', '0006_require_contenttypes_0002'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='CommitFest',
            fields=[
                ('id', models.AutoField(verbose_name='ID', serialize=False, auto_created=True, primary_key=True)),
                ('name', models.CharField(unique=True, max_length=100)),
                ('status', models.IntegerField(default=1, choices=[(1, b'Future'), (2, b'Open'), (3, b'In Progress'), (4, b'Closed')])),
                ('startdate', models.DateField(null=True, blank=True)),
                ('enddate', models.DateField(null=True, blank=True)),
            ],
            options={
                'ordering': ('-startdate',),
                'verbose_name_plural': 'Commitfests',
            },
        ),
        migrations.CreateModel(
            name='Committer',
            fields=[
                ('user', models.OneToOneField(primary_key=True, serialize=False, to=settings.AUTH_USER_MODEL)),
                ('active', models.BooleanField(default=True)),
            ],
            options={
                'ordering': ('user__last_name', 'user__first_name'),
            },
        ),
        migrations.CreateModel(
            name='MailThread',
            fields=[
                ('id', models.AutoField(verbose_name='ID', serialize=False, auto_created=True, primary_key=True)),
                ('messageid', models.CharField(unique=True, max_length=1000)),
                ('subject', models.CharField(max_length=500)),
                ('firstmessage', models.DateTimeField()),
                ('firstauthor', models.CharField(max_length=500)),
                ('latestmessage', models.DateTimeField()),
                ('latestauthor', models.CharField(max_length=500)),
                ('latestsubject', models.CharField(max_length=500)),
                ('latestmsgid', models.CharField(max_length=1000)),
            ],
            options={
                'ordering': ('firstmessage',),
            },
        ),
        migrations.CreateModel(
            name='MailThreadAnnotation',
            fields=[
                ('id', models.AutoField(verbose_name='ID', serialize=False, auto_created=True, primary_key=True)),
                ('date', models.DateTimeField(auto_now_add=True)),
                ('msgid', models.CharField(max_length=1000)),
                ('annotationtext', models.TextField(max_length=2000)),
                ('mailsubject', models.CharField(max_length=500)),
                ('maildate', models.DateTimeField()),
                ('mailauthor', models.CharField(max_length=500)),
                ('mailthread', models.ForeignKey(to='commitfest.MailThread')),
                ('user', models.ForeignKey(to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'ordering': ('date',),
            },
        ),
        migrations.CreateModel(
            name='MailThreadAttachment',
            fields=[
                ('id', models.AutoField(verbose_name='ID', serialize=False, auto_created=True, primary_key=True)),
                ('messageid', models.CharField(max_length=1000)),
                ('attachmentid', models.IntegerField()),
                ('filename', models.CharField(max_length=1000, blank=True)),
                ('date', models.DateTimeField()),
                ('author', models.CharField(max_length=500)),
                ('ispatch', models.NullBooleanField()),
                ('mailthread', models.ForeignKey(to='commitfest.MailThread')),
            ],
            options={
                'ordering': ('-date',),
            },
        ),
        migrations.CreateModel(
            name='Patch',
            fields=[
                ('id', models.AutoField(verbose_name='ID', serialize=False, auto_created=True, primary_key=True)),
                ('name', models.CharField(max_length=500, verbose_name=b'Description')),
                ('wikilink', models.URLField(default=b'', null=True, blank=True)),
                ('gitlink', models.URLField(default=b'', null=True, blank=True)),
                ('created', models.DateTimeField(auto_now_add=True)),
                ('modified', models.DateTimeField()),
                ('lastmail', models.DateTimeField(null=True, blank=True)),
                ('authors', models.ManyToManyField(related_name='patch_author', to=settings.AUTH_USER_MODEL, blank=True)),
            ],
            options={
                'verbose_name_plural': 'patches',
            },
            bases=(models.Model, pgcommitfest.commitfest.util.DiffableModel),
        ),
        migrations.CreateModel(
            name='PatchHistory',
            fields=[
                ('id', models.AutoField(verbose_name='ID', serialize=False, auto_created=True, primary_key=True)),
                ('date', models.DateTimeField(auto_now_add=True)),
                ('what', models.CharField(max_length=500)),
                ('by', models.ForeignKey(to=settings.AUTH_USER_MODEL)),
                ('patch', models.ForeignKey(to='commitfest.Patch')),
            ],
            options={
                'ordering': ('-date',),
            },
        ),
        migrations.CreateModel(
            name='PatchOnCommitFest',
            fields=[
                ('id', models.AutoField(verbose_name='ID', serialize=False, auto_created=True, primary_key=True)),
                ('enterdate', models.DateTimeField()),
                ('leavedate', models.DateTimeField(null=True, blank=True)),
                ('status', models.IntegerField(default=1, choices=[(1, b'Needs review'), (2, b'Waiting on Author'), (3, b'Ready for Committer'), (4, b'Committed'), (5, b'Moved to next CF'), (6, b'Rejected'), (7, b'Returned with feedback')])),
                ('commitfest', models.ForeignKey(to='commitfest.CommitFest')),
                ('patch', models.ForeignKey(to='commitfest.Patch')),
            ],
            options={
                'ordering': ('-commitfest__startdate',),
            },
        ),
        migrations.CreateModel(
            name='PatchStatus',
            fields=[
                ('status', models.IntegerField(serialize=False, primary_key=True)),
                ('statusstring', models.TextField(max_length=50)),
                ('sortkey', models.IntegerField(default=10)),
            ],
        ),
        migrations.CreateModel(
            name='Topic',
            fields=[
                ('id', models.AutoField(verbose_name='ID', serialize=False, auto_created=True, primary_key=True)),
                ('topic', models.CharField(max_length=100)),
            ],
        ),
        migrations.AddField(
            model_name='patch',
            name='commitfests',
            field=models.ManyToManyField(to='commitfest.CommitFest', through='commitfest.PatchOnCommitFest'),
        ),
        migrations.AddField(
            model_name='patch',
            name='committer',
            field=models.ForeignKey(blank=True, to='commitfest.Committer', null=True),
        ),
        migrations.AddField(
            model_name='patch',
            name='reviewers',
            field=models.ManyToManyField(related_name='patch_reviewer', to=settings.AUTH_USER_MODEL, blank=True),
        ),
        migrations.AddField(
            model_name='patch',
            name='topic',
            field=models.ForeignKey(to='commitfest.Topic'),
        ),
        migrations.AddField(
            model_name='mailthread',
            name='patches',
            field=models.ManyToManyField(to='commitfest.Patch'),
        ),
        migrations.AlterUniqueTogether(
            name='patchoncommitfest',
            unique_together=set([('patch', 'commitfest')]),
        ),
        migrations.AlterUniqueTogether(
            name='mailthreadattachment',
            unique_together=set([('mailthread', 'messageid')]),
        ),
    ]
