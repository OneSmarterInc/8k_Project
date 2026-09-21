import os
import django
from pathlib import Path

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from watcher.models import Filing, FilingDocument

print("--- Checking Filing Date ---")
filings_by_date = Filing.objects.filter(filing_date__gte='2026-09-15').order_by('-filing_date')
print(f'Total filings by filing_date >= Sep 15: {filings_by_date.count()}')
for f in filings_by_date:
    print(f' - Ticker: {f.company.ticker} | Filing Date: {f.filing_date} | Accepted: {f.accepted_at}')

print("\n--- Checking Downloaded Documents ---")
docs = FilingDocument.objects.filter(filing__filing_date__gte='2026-09-15')
print(f'Total FilingDocuments for filings >= Sep 15: {docs.count()}')

print("\n--- Checking physical Downloads Folder ---")
downloads_dir = Path('downloads')
if downloads_dir.exists():
    for company_dir in downloads_dir.iterdir():
        if company_dir.is_dir():
            print(f"Company Folder: {company_dir.name}")
            for filing_dir in company_dir.iterdir():
                if filing_dir.is_dir():
                    files = list(filing_dir.glob('*'))
                    print(f"  -> Filing Folder: {filing_dir.name} ({len(files)} files)")
else:
    print("No downloads directory found.")
