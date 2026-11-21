"""The kept consent for Claude tokens (`llm_connector.TransferConsent`, scope `claude`). A user
who created a token has agreed already: their consent starts at their first token's stamp."""

from django.db import migrations


def keep_the_consent_of_tokens(apps, schema_editor):
    AccessToken = apps.get_model("jac", "AccessToken")
    TransferConsent = apps.get_model("llm_connector", "TransferConsent")
    first = {}
    for user_id, at in AccessToken.objects.values_list(
        "user_id", "transfer_consent_at"
    ):
        first[user_id] = min(at, first.get(user_id, at))
    TransferConsent.objects.bulk_create(
        TransferConsent(user_id=u, scope="claude", given_at=at)
        for u, at in first.items()
    )


class Migration(migrations.Migration):
    dependencies = [
        ("jac", "0004_accesstoken_transfer_consent"),
        ("llm_connector", "0004_transferconsent"),
    ]

    operations = [
        migrations.RunPython(keep_the_consent_of_tokens, migrations.RunPython.noop),
    ]
