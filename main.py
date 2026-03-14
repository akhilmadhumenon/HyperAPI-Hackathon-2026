"""
Main orchestrator for the Financial Gauntlet needle detection pipeline.

Flow:
  1. Split gauntlet.pdf into document segments (deterministic, pypdf-based)
  2. Initialize SQLite schema
  3. Ingest all segments (parse text → normalize → insert into DB)
  4. Run all 20 validation checks
  5. Build and save submission JSON

Usage:
  python main.py                # full pipeline
  python main.py --split-only   # only re-run segment detection
  python main.py --ingest-only  # only re-run ingestion (uses existing segments.json)
  python main.py --validate-only # only re-run validation (uses existing DB)
  python main.py --stats        # print DB stats without re-running
"""
import argparse
import json
import os
import sqlite3
import sys

import db_setup
import custom_split
import ingestion_engine
import validation_engine
import findings_builder

BASE_DIR = os.path.dirname(__file__)


def print_db_stats(db_path: str = db_setup.DB_PATH):
    conn = sqlite3.connect(db_path)
    tables = [
        'vendor_master', 'invoices', 'invoice_line_items',
        'purchase_orders', 'po_line_items',
        'bank_statements', 'bank_transactions',
        'expense_reports', 'expense_entries',
        'credit_notes', 'debit_notes',
    ]
    print("\n── DB Stats ──────────────────────────────────────")
    for t in tables:
        try:
            count = conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
            print(f"  {t:<28} {count:>6} rows")
        except Exception as e:
            print(f"  {t:<28} ERROR: {e}")
    conn.close()


def main():
    parser = argparse.ArgumentParser(description='Financial Gauntlet needle detector')
    parser.add_argument('--split-only',    action='store_true', help='Only run PDF splitting')
    parser.add_argument('--ingest-only',   action='store_true', help='Only run ingestion')
    parser.add_argument('--validate-only', action='store_true', help='Only run validation')
    parser.add_argument('--stats',         action='store_true', help='Print DB stats and exit')
    parser.add_argument('--db',            default=db_setup.DB_PATH, help='SQLite DB path')
    parser.add_argument('--pdf',           default=ingestion_engine.PDF_PATH, help='PDF path')
    parser.add_argument('--out',           default=findings_builder.SUBMISSION_PATH, help='Output JSON path')
    args = parser.parse_args()

    if args.stats:
        print_db_stats(args.db)
        return

    # ── Step 1: Split ────────────────────────────────────────────────────────
    if not args.validate_only and not args.ingest_only:
        print("\n[1/4] Splitting PDF into segments...")
        segs = custom_split.split_pdf(args.pdf)
        custom_split.save_segments(segs)

    elif not args.validate_only:
        segs_path = custom_split.SEGMENTS_JSON
        if os.path.exists(segs_path):
            segs = custom_split.load_segments(segs_path)
            print(f"[1/4] Loaded {len(segs)} segments from {segs_path}")
        else:
            print("[1/4] segments.json not found — running split first...")
            segs = custom_split.split_pdf(args.pdf)
            custom_split.save_segments(segs)

    # ── Step 2: Init DB ──────────────────────────────────────────────────────
    if not args.validate_only:
        print("\n[2/4] Initializing database schema...")
        db_setup.init_db(args.db)

    # ── Step 3: Ingest ───────────────────────────────────────────────────────
    if not args.validate_only:
        print("\n[3/4] Ingesting documents...")
        ingestion_engine.run_ingestion(
            segments_path=custom_split.SEGMENTS_JSON,
            pdf_path=args.pdf,
            db_path=args.db,
        )
        print_db_stats(args.db)

    # ── Step 4: Validate ─────────────────────────────────────────────────────
    if not args.split_only and not args.ingest_only:
        print("\n[4/4] Running validation checks...")
        raw_findings = validation_engine.run_all_validations(args.db)

        submission = findings_builder.build_submission(raw_findings)
        path = findings_builder.save_submission(submission, args.out)

        findings_builder.print_summary(submission)
        print(f"\n  Submission saved → {path}")

        # Print first few findings as sample
        if submission['findings']:
            print("\n── Sample findings (first 5) ──────────────────────────")
            for f in submission['findings'][:5]:
                print(f"  [{f['finding_id']}] {f['category']}")
                print(f"    refs: {f['document_refs']}")
                print(f"    pages: {f['pages']}")
                print(f"    {f['description'][:80]}")
                print()


if __name__ == '__main__':
    main()
