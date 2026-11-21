"""Tokens need the user's consent to the transfer (`AccessToken.transfer_consent_at`). The tokens
minted before it have none, so they are revoked: deleting a row is how a token is revoked, and
the user creates a new one in Account → LLM → Claude, agreeing first."""

import django.utils.timezone
from django.db import migrations, models


def revoke_tokens_without_consent(apps, schema_editor):
    apps.get_model("jac", "AccessToken").objects.all().delete()


class Migration(migrations.Migration):
    dependencies = [
        ("jac", "0003_career_change"),
    ]

    operations = [
        migrations.RunPython(revoke_tokens_without_consent, migrations.RunPython.noop),
        migrations.AddField(
            model_name="accesstoken",
            name="transfer_consent_at",
            field=models.DateTimeField(default=django.utils.timezone.now),
            preserve_default=False,
        ),
    ]
