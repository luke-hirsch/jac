# Handwritten: the autodetector can't add the non-null `job_application` FK. Old
# GenerationRun rows are stub-era dev data with no application to attach to, so they
# are wiped before the column lands (the bogus `default=0` never reaches a row).

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models

import jac.models


def _wipe_generation_runs(apps, schema_editor):
    apps.get_model("jac", "GenerationRun").objects.all().delete()


class Migration(migrations.Migration):

    dependencies = [
        ('jac', '0005_generationrun'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='ApplicationLayout',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=50)),
                ('template', models.FileField(blank=True, upload_to='application_layouts')),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='layouts', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'ordering': ['name'],
            },
        ),
        migrations.AddConstraint(
            model_name='applicationlayout',
            constraint=models.UniqueConstraint(models.F('user'), models.F('name'), name='unique_layout_per_user'),
        ),
        migrations.AddField(
            model_name='jobposting',
            name='active',
            field=models.BooleanField(default=True),
        ),
        migrations.CreateModel(
            name='JobApplication',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('cv_content', models.JSONField(blank=True, default=dict)),
                ('cover_letter', models.TextField(blank=True)),
                ('status', models.CharField(choices=[('draft', 'Draft'), ('sent', 'Sent'), ('response', 'Response from Posting'), ('follow_up', 'Follow-up sent'), ('inactive', 'Inactive')], default='draft', max_length=12)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('layout', models.ForeignKey(default=jac.models.default_application_layout, on_delete=django.db.models.deletion.SET_DEFAULT, related_name='applications', to='jac.applicationlayout')),
                ('posting', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='applications', to='jac.jobposting')),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='job_applications', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'ordering': ['-created_at'],
            },
        ),
        migrations.RunPython(_wipe_generation_runs, migrations.RunPython.noop),
        migrations.RemoveField(
            model_name='generationrun',
            name='user',
        ),
        migrations.RemoveField(
            model_name='generationrun',
            name='job_posting',
        ),
        migrations.RemoveField(
            model_name='generationrun',
            name='posting_text',
        ),
        migrations.AddField(
            model_name='generationrun',
            name='job_application',
            field=models.ForeignKey(default=0, on_delete=django.db.models.deletion.CASCADE, related_name='runs', to='jac.jobapplication'),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name='generationrun',
            name='evaluation',
            field=models.TextField(blank=True, default=''),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name='generationrun',
            name='score',
            field=models.CharField(blank=True, default='', max_length=50),
            preserve_default=False,
        ),
    ]
