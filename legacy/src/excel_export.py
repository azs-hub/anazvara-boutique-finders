"""Excel export for discovered boutiques."""

from __future__ import annotations

from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUTPUT_PATH = PROJECT_ROOT / "output" / "boutiques.xlsx"

COLUMNS = [
    ("id", "ID"),
    ("name", "Boutique Name"),
    ("city", "City"),
    ("address", "Address"),
    ("website", "Website"),
    ("instagram", "Instagram"),
    ("email", "Email"),
    ("phone", "Phone"),
    ("source_url", "Source URL"),
    ("date_discovered", "Date Discovered"),
]


def export_boutiques(
    records: list[dict],
    output_path: Path | None = None,
) -> Path:
    """Write boutique rows to ``output/boutiques.xlsx``.

    Args:
        records: Mappings with the database field names.
        output_path: Optional override; defaults to ``output/boutiques.xlsx``.

    Returns:
        Path to the written workbook.
    """
    path = Path(output_path) if output_path else DEFAULT_OUTPUT_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    frame = pd.DataFrame(records)
    for source_name, _label in COLUMNS:
        if source_name not in frame.columns:
            frame[source_name] = None
    frame = frame[[source_name for source_name, _label in COLUMNS]]
    frame.columns = [label for _source_name, label in COLUMNS]
    frame.to_excel(path, index=False, engine="openpyxl")
    return path
