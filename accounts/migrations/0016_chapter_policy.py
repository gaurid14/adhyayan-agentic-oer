from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0015_merge_20251112_1811"),
    ]

    operations = [
        migrations.CreateModel(
            name="ChapterPolicy",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("deadline", models.DateTimeField(blank=True, null=True)),
                ("current_deadline", models.DateTimeField(blank=True, null=True)),
                ("min_contributions", models.PositiveIntegerField(default=1)),
                ("max_extensions", models.PositiveIntegerField(default=0)),
                ("max_days_per_extension", models.PositiveIntegerField(default=0)),
                ("extensions_used", models.PositiveIntegerField(default=0)),
                ("chapter", models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name="policy", to="accounts.chapter")),
            ],
        ),
        migrations.CreateModel(
            name="ChapterDeadlineExtension",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("extended_at", models.DateTimeField(auto_now_add=True)),
                ("days_extended", models.PositiveIntegerField()),
                ("old_deadline", models.DateTimeField()),
                ("new_deadline", models.DateTimeField()),
                ("note", models.TextField(blank=True)),
                ("extended_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="chapter_deadline_extensions", to=settings.AUTH_USER_MODEL)),
                ("policy", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="extensions", to="accounts.chapterpolicy")),
            ],
            options={"ordering": ["-extended_at"]},
        ),
    ]
