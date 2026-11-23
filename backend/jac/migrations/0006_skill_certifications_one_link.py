"""A skill and a certification were linked twice: the skill's `certification` and the
certification's `skills`, each edited on its own page, so one side often stayed empty and the
review flagged a link that existed. The certification's list is the one link now. Every
skill-side link moves into it before the field goes; links that were already on both sides
stay one row."""

from django.db import migrations


def move_links(apps, schema_editor):
    Skill = apps.get_model("jac", "Skill")
    Certification = apps.get_model("jac", "Certification")
    Link = Certification.skills.through
    Link.objects.bulk_create(
        [
            Link(certification_id=cert, skill_id=skill)
            for skill, cert in Skill.objects.filter(
                certification__isnull=False
            ).values_list("pk", "certification_id")
        ],
        ignore_conflicts=True,
    )


class Migration(migrations.Migration):
    dependencies = [
        ("jac", "0005_claude_transfer_consent"),
    ]

    operations = [
        migrations.RunPython(move_links, migrations.RunPython.noop),
        migrations.RemoveField(
            model_name="skill",
            name="certification",
        ),
    ]
