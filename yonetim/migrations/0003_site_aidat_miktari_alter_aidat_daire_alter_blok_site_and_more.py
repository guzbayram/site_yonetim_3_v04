# Generated by Django 5.2.1 on 2025-05-08 17:44

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("yonetim", "0002_alter_site_yonetici_tel"),
    ]

    operations = [
        migrations.AddField(
            model_name="site",
            name="aidat_miktari",
            field=models.DecimalField(
                blank=True,
                decimal_places=2,
                max_digits=10,
                null=True,
                verbose_name="Aylık Aidat Miktarı",
            ),
        ),
        migrations.AlterField(
            model_name="aidat",
            name="daire",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="aidatlar",
                to="yonetim.daire",
            ),
        ),
        migrations.AlterField(
            model_name="blok",
            name="site",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="bloklar",
                to="yonetim.site",
            ),
        ),
        migrations.AlterField(
            model_name="daire",
            name="blok",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="daireler",
                to="yonetim.blok",
            ),
        ),
        migrations.AlterField(
            model_name="daire",
            name="kullanici",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="daireleri",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AlterField(
            model_name="gider",
            name="site",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="giderler",
                to="yonetim.site",
            ),
        ),
        migrations.AlterField(
            model_name="gider",
            name="tutar",
            field=models.DecimalField(
                decimal_places=2, max_digits=10, verbose_name="Tutar / Amount"
            ),
        ),
    ]
