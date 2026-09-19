from watcher.models import Filing
from django.conf import settings
import json
from pathlib import Path

accessions = [
    '0000927628-26-000134', '0000927628-26-000132'
]
filings = Filing.objects.filter(accession_number__in=accessions)
print(f'Deleting {filings.count()} filings from database for Capital One...')
filings.delete()

base = getattr(settings, 'SEC_DOWNLOAD_DIR', Path(settings.BASE_DIR) / 'downloads')
reg_path = Path(base) / 'downloaded_files.json'
print(f'Registry path: {reg_path}')
if reg_path.exists():
    records = json.loads(reg_path.read_text(encoding='utf-8'))
    original_len = len(records)
    records = [r for r in records if not any(acc in r for acc in accessions)]
    reg_path.write_text(json.dumps(records, indent=4), encoding='utf-8')
    print(f'Removed {original_len - len(records)} entries from download registry.')
