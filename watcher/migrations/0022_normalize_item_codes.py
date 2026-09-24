"""
W-036 data migration: rewrite "('1.01', '9.01')" rows to "1.01;9.01".
Logic is inlined so this migration never changes if the helper changes.
"""

import re

from django.db import migrations

_SPLIT_RE = re.compile(r"[;,]")
_STRIP_CHARS = " \t\r\n'\"()[]"


def _canonical(text):
    if not text:
        return ""
    result, seen = [], set()
    for part in _SPLIT_RE.split(text):
        part = part.strip(_STRIP_CHARS)
        if part and part not in seen:
            seen.add(part)
            result.append(part)
    return ";".join(result)


def normalize_item_codes(apps, schema_editor):
    Filing = apps.get_model("watcher", "Filing")
    for filing in Filing.objects.only(
        "id", "sec_item_codes", "parsed_item_codes"
    ).iterator():
        sec = _canonical(filing.sec_item_codes)
        parsed = _canonical(filing.parsed_item_codes)
        if sec != filing.sec_item_codes or parsed != filing.parsed_item_codes:
            Filing.objects.filter(pk=filing.pk).update(
                sec_item_codes=sec,
                parsed_item_codes=parsed,
            )


class Migration(migrations.Migration):

    dependencies = [
        ("watcher", "0021_automationrun_ambiguous_amendments_count"),
    ]

    operations = [
        migrations.RunPython(normalize_item_codes, migrations.RunPython.noop),
    ]