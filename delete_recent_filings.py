import os
import json
import django

from datetime import date
from pathlib import Path

os.environ.setdefault(
    "DJANGO_SETTINGS_MODULE",
    "config.settings",
)

django.setup()

from watcher.knowledge_base.models import Filing
from watcher.services.download_registry import DownloadRegistry


START_DATE = date(2026, 9, 15)


def main():

    # --------------------------------------------------
    # 1. Find filings from Sep 15, 2026 onward
    # --------------------------------------------------

    filings_to_delete = (
        Filing.objects
        .filter(filing_date__gte=START_DATE)
        .select_related("company")
        .prefetch_related("documents")
    )

    count = filings_to_delete.count()

    print(
        f"\nFound {count} filings "
        f"with filing_date >= {START_DATE}."
    )

    if count == 0:
        print("Nothing to delete.")
        return

    # --------------------------------------------------
    # 2. Preview exactly what will be deleted
    # --------------------------------------------------

    print("\nFilings to delete:")

    for filing in filings_to_delete:
        print(
            filing.id,
            filing.company.ticker,
            filing.form,
            filing.accession_number,
            filing.filing_date,
            filing.accepted_at,
        )

    registry = DownloadRegistry()

    registry_keys_to_remove = set()
    paths_to_delete = set()

    # --------------------------------------------------
    # 3. Collect registry entries and physical paths
    # --------------------------------------------------

    for filing in filings_to_delete:

        registry_keys_to_remove.add(
            registry.make_key(
                filing.company.cik,
                filing.accession_number,
                filing.sequence,
            )
        )

        if filing.local_path:
            paths_to_delete.add(
                Path(filing.local_path)
            )

        for document in filing.documents.all():
            if document.local_path:
                paths_to_delete.add(
                    Path(document.local_path)
                )

    # --------------------------------------------------
    # 4. Delete physical files
    # --------------------------------------------------

    deleted_files = 0

    for path in paths_to_delete:

        if path.exists() and path.is_file():
            path.unlink()
            deleted_files += 1

    print(
        f"\nDeleted {deleted_files} physical files."
    )

    # --------------------------------------------------
    # 5. Remove DownloadRegistry entries
    # --------------------------------------------------

    records = registry._load()

    original_count = len(records)

    remaining_records = [
        record
        for record in records
        if record not in registry_keys_to_remove
    ]

    registry.path.write_text(
        json.dumps(
            remaining_records,
            indent=4,
        ),
        encoding="utf-8",
    )

    print(
        "Removed "
        f"{original_count - len(remaining_records)} "
        "Download Registry entries."
    )

    # --------------------------------------------------
    # 6. Delete database records
    # --------------------------------------------------

    deleted_rows, details = (
        filings_to_delete.delete()
    )

    print(
        f"Deleted {deleted_rows} total "
        "database rows."
    )

    print(details)

    print(
        "\nSUCCESS: filings from "
        "September 15, 2026 onward removed."
    )


if __name__ == "__main__":
    main()