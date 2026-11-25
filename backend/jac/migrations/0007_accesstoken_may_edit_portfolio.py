"""A per-token switch for portfolio writes over the connector. Every token made before it starts
off, like a new one: the user switches it on for the tokens that should have it."""

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("jac", "0006_skill_certifications_one_link"),
    ]

    operations = [
        migrations.AddField(
            model_name="accesstoken",
            name="may_edit_portfolio",
            field=models.BooleanField(default=False),
        ),
    ]
