"""Environment smoke test for Anazvara Boutique Scraper.

This increment only verifies Python, SQLite, and Excel export. Discovery and
website scraping are not implemented yet.
"""

from __future__ import annotations

import platform
import sys
from pathlib import Path

SRC_DIR = Path(__file__).resolve().parent
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from database import BoutiqueDatabase
from excel_export import export_boutiques
from scraper import BoutiqueRecord, today_iso


def run_smoke_test() -> int:
    """Initialize SQLite, insert a sample boutique, and export Excel."""
    print("Anazvara Boutique Scraper — environment check")
    print(f"Python: {sys.version.split()[0]}")
    print(f"Platform: {platform.system()} {platform.machine()}")
    print(f"Executable: {sys.executable}")

    database = BoutiqueDatabase()
    db_path = database.initialize()
    print(f"SQLite ready: {db_path}")

    sample = BoutiqueRecord(
        name="Sample Mumbai Boutique",
        city="Mumbai",
        address="123 Linking Road, Bandra West, Mumbai",
        website="https://www.sample-mumbai-boutique.example",
        instagram="@samplemumbaiboutique",
        email="hello@sample-mumbai-boutique.example",
        phone="+91 98765 43210",
        source_url="https://example.com/source/sample-mumbai-boutique",
        date_discovered=today_iso(),
    )
    inserted_id = database.insert_if_new(sample)
    if inserted_id is None:
        print("Sample boutique already exists; skipped insert (deduplication).")
    else:
        print(f"Inserted sample boutique with ID {inserted_id}.")

    duplicate_id = database.insert_if_new(sample)
    if duplicate_id is None:
        print("Duplicate insert correctly rejected.")
    else:
        print("ERROR: duplicate boutique was inserted.")
        return 1

    records = database.fetch_all()
    print(f"Boutiques stored: {database.count()}")

    excel_path = export_boutiques(records)
    print(f"Excel written: {excel_path}")
    print("Smoke test passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(run_smoke_test())
