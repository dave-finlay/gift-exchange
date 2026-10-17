# Gift exchanges become top-level: each has an owner, and a draft may not
# have a circle or a gift date yet. Existing ones take their circle's owner.

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


def owners_from_circles(apps, schema_editor):
    Exchange = apps.get_model("exchanges", "Exchange")
    for exchange in Exchange.objects.select_related("circle"):
        exchange.owner_id = exchange.circle.owner_id
        exchange.save(update_fields=["owner"])


class Migration(migrations.Migration):
    dependencies = [
        ("exchanges", "0002_remove_person_active"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="exchange",
            name="owner",
            field=models.ForeignKey(
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="exchanges",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.RunPython(owners_from_circles, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="exchange",
            name="owner",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="exchanges",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AlterField(
            model_name="exchange",
            name="circle",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="exchanges",
                to="exchanges.circle",
            ),
        ),
        migrations.AlterField(
            model_name="exchange",
            name="gift_date",
            field=models.DateField(blank=True, null=True),
        ),
    ]
