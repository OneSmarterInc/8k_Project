import os
import json
import django
from datetime import datetime, timezone
from pathlib import Path

# Setup Django environment
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()

from watcher.models import Filing
from watcher.services.download_registry import DownloadRegistry

def main():
    # 1. Define the date range (Sept 14, 2026 to now)
    start_date = datetime(2026, 9, 14, tzinfo=timezone.utc)
    
    # 2. Get the target filings
    filings_to_delete = Filing.objects.filter(accepted_at__gte=start_date)
    count = filings_to_delete.count()
    print(f"Found {count} filings accepted since Sept 14, 2026.")
    
    if count == 0:
        print("Nothing to delete.")
        return

    registry = DownloadRegistry()
    registry_keys_to_remove = set()
    paths_to_delete = set()
    
    # 3. Collect physical files and registry keys
    for filing in filings_to_delete:
        key = registry.make_key(filing.company.cik, filing.accession_number, filing.sequence)
        registry_keys_to_remove.add(key)
        
        if filing.local_path:
            paths_to_delete.add(Path(filing.local_path))
            
        for doc in filing.documents.all():
            if doc.local_path:
                paths_to_delete.add(Path(doc.local_path))
                
    # 4. Remove the registry keys from downloaded_files.json
    records = registry._load()
    original_count = len(records)
    new_records = [r for r in records if r not in registry_keys_to_remove]
    
    registry.path.write_text(json.dumps(new_records, indent=4), encoding="utf-8")
    print(f"Removed {original_count - len(new_records)} entries from the Download Registry.")
    
    # 5. Delete the physical files
    deleted_files = 0
    for path in paths_to_delete:
        if path.exists() and path.is_file():
            path.unlink()
            deleted_files += 1
    print(f"Deleted {deleted_files} physical files from D:\\SECFILE\\")

    # 6. Delete from the Database
    deleted_db_count, _ = filings_to_delete.delete()
    print(f"Deleted {deleted_db_count} total rows from the database.")
    
    print("\nSUCCESS! Only filings since Sept 14, 2026 have been removed.")

if __name__ == "__main__":
    main()