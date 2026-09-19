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


START_DATE = date(2026, 9, 16)


def main():

    filings = Filing.objects.filter(
        filing_date__gte=START_DATE
    )

    count = filings.count()

    print(
        f"Found filings from {START_DATE}: {count}"
    )

    if count == 0:
        print("Nothing to delete.")
        return

    registry = DownloadRegistry()

    registry_keys = set()
    file_paths = set()

    for filing in filings:

        print(
            "Deleting:",
            filing.accession_number,
            filing.filing_date,
        )

        registry_keys.add(
            registry.make_key(
                filing.cik,
                filing.accession_number,
                filing.sequence,
            )
        )

        if filing.local_path:
            file_paths.add(
                Path(filing.local_path)
            )

        for document in filing.documents.all():
            if document.local_path:
                file_paths.add(
                    Path(document.local_path)
                )


    # Remove registry entries only for these filings

    records = registry._load()

    remaining = [
        record
        for record in records
        if record not in registry_keys
    ]

    registry.path.write_text(
        json.dumps(
            remaining,
            indent=4,
        ),
        encoding="utf-8",
    )

    print(
        "Registry removed:",
        len(records) - len(remaining),
    )


    # Delete downloaded files

    deleted_files = 0

    for path in file_paths:

        if path.exists() and path.is_file():
            path.unlink()
            deleted_files += 1

    print(
        "Physical files deleted:",
        deleted_files,
    )


    # Delete database records

    deleted_rows, details = filings.delete()

    print(
        "Database deleted:",
        deleted_rows,
    )

    print(details)

    print(
        "\nCleanup completed."
    )


if __name__ == "__main__":
    main()