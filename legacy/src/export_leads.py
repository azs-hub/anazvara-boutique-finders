"""Export clean stockist leads from an existing benchmark JSON.

Usage:
    python src/export_leads.py output/benchmark_batch_20260927_135544.json

Offline only. Does not search, scrape, enrich, or call Qwen.
Does not modify the original benchmark file.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

SRC_DIR = Path(__file__).resolve().parent
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from lead_export import export_leads, print_export_report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Export CRM-ready stockist leads from an existing benchmark."
    )
    parser.add_argument(
        "benchmark",
        help="Path to an existing benchmark JSON file",
    )
    parser.add_argument(
        "--output-dir",
        help="Directory for CSV/JSON exports (default: same directory as the benchmark)",
    )
    return parser


def run_export_main(args: argparse.Namespace) -> int:
    path = Path(args.benchmark)
    if not path.is_file():
        print(f"Benchmark file not found: {path}", file=sys.stderr)
        return 1
    try:
        result = export_leads(path, output_dir=args.output_dir)
    except (OSError, ValueError, TypeError) as exc:
        print(f"Export failed: {exc}", file=sys.stderr)
        return 1
    print_export_report(result)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return run_export_main(args)


if __name__ == "__main__":
    raise SystemExit(main())
