# W-037 (R-08 / R-09): intraday interval polling + nightly sweep.
#
# Purely additive. Every field is either defaulted or nullable, so the
# existing ScheduleConfig row is untouched and no backfill is required.
# This migration is safe to reverse.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('watcher', '0023_smtpconfig'),
    ]

    operations = [
        migrations.AddField(
            model_name='scheduleconfig',
            name='interval_minutes',
            field=models.PositiveIntegerField(default=20),
        ),
        migrations.AddField(
            model_name='scheduleconfig',
            name='active_window_start',
            field=models.TimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='scheduleconfig',
            name='active_window_end',
            field=models.TimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='scheduleconfig',
            name='nightly_sweep',
            field=models.BooleanField(default=True),
        ),
    ]
