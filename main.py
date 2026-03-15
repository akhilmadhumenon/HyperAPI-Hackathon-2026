#!/usr/bin/env python3
"""
Financial Gauntlet — Error Detection Pipeline
===============================================
Processes a 1000-page AP document bundle and detects 200 deliberate errors
across 20 categories (easy/medium/evil tiers).

Usage:
    python main.py --pdf invoices.pdf --output submission.json --team-id team_42
    python main.py --pdf invoices.pdf --output submission.json --use-llm  # with LLM enhancement
"""
import argparse
import json
import os
import sys
import time
from pathlib import Path
from dotenv import load_dotenv

# Load env variables
load_dotenv()

from src.extractor import extract_all_pages, split_into_documents
from src.parser import parse_all_documents
from src.llm_parser import enhance_parsing_with_llm
from src.reference_store import build_reference_stores
from src.detectors.easy import run_easy_detectors
from src.detectors.medium import run_medium_detectors
from src.detectors.evil import run_evil_detectors
from src.assembler import assemble_submission, save_submission


def main():
    parser = argparse.ArgumentParser(description="Financial Gauntlet — Error Detection Pipeline")
    parser.add_argument("--pdf", type=str, default="invoices.pdf",
                       help="Path to the gauntlet PDF file")
    parser.add_argument("--output", type=str, default="submission.json",
                       help="Output submission JSON file")
    parser.add_argument("--team-id", type=str, default=os.environ.get("TEAM_ID", "team_42"),
                       help="Team identifier")
    parser.add_argument("--max-pages", type=int, default=None,
                       help="Max pages to process (for testing)")
    parser.add_argument("--use-llm", action="store_true",
                       help="Use LLM to enhance parsing of poorly-extracted documents")
    parser.add_argument("--save-intermediate", action="store_true",
                       help="Save intermediate data to data/ directory")
    parser.add_argument("--force-extract", action="store_true",
                       help="Force re-extraction of PDF pages even if cache exists")
    
    args = parser.parse_args()
    
    print("=" * 70)
    print("🏴 FINANCIAL GAUNTLET — Error Detection Pipeline")
    print("=" * 70)
    print(f"  PDF: {args.pdf}")
    print(f"  Output: {args.output}")
    print(f"  Team: {args.team_id}")
    print(f"  LLM enhancement: {'ON' if args.use_llm else 'OFF'}")
    print("=" * 70)
    
    start_time = time.time()
    
    # ──────────────────────────────────────────────────────────────────────
    # PHASE 1: PDF Extraction (with caching)
    # ──────────────────────────────────────────────────────────────────────
    print("\n" + "─" * 50)
    print("📄 PHASE 1: PDF Extraction")
    print("─" * 50)
    
    import pickle
    os.makedirs("data", exist_ok=True)
    page_cache_path = "data/extracted_pages.pkl"
    
    # Check if we should use cache
    if not args.force_extract and os.path.exists(page_cache_path):
        print(f"  📦 Loading extracted pages from cache: {page_cache_path}")
        with open(page_cache_path, "rb") as f:
            pages = pickle.load(f)
        # Handle max_pages if cache has more than requested
        if args.max_pages:
            pages = pages[:args.max_pages]
        print(f"  ✅ Loaded {len(pages)} pages from cache")
    else:
        pages = extract_all_pages(args.pdf, max_pages=args.max_pages)
        # Save to cache if we extracted all (or significant amount)
        if not args.max_pages or args.max_pages >= 900:
            print(f"  💾 Saving extracted pages to cache: {page_cache_path}")
            with open(page_cache_path, "wb") as f:
                pickle.dump(pages, f)
    
    # Always re-run splitting as it's fast and classification logic might change
    documents = split_into_documents(pages)
    
    # ──────────────────────────────────────────────────────────────────────
    # PHASE 2: Document Parsing
    # ──────────────────────────────────────────────────────────────────────
    print("\n" + "─" * 50)
    print("🔍 PHASE 2: Document Parsing")
    print("─" * 50)
    
    documents = parse_all_documents(documents)
    
    # Optional LLM enhancement
    if args.use_llm:
        documents = enhance_parsing_with_llm(documents, use_llm=True)
    
    # Save intermediate data
    if args.save_intermediate:
        os.makedirs("data", exist_ok=True)
        # Save document summary
        summary = []
        for doc in documents:
            s = {
                "doc_id": doc.doc_id,
                "doc_type": doc.doc_type.value,
                "pages": doc.pages,
            }
            if doc.parsed:
                if hasattr(doc.parsed, 'vendor_name'):
                    s["vendor_name"] = doc.parsed.vendor_name
                if hasattr(doc.parsed, 'grand_total'):
                    s["grand_total"] = doc.parsed.grand_total
                if hasattr(doc.parsed, 'po_ref'):
                    s["po_ref"] = doc.parsed.po_ref
                if hasattr(doc.parsed, 'line_items'):
                    s["line_item_count"] = len(doc.parsed.line_items)
                if hasattr(doc.parsed, 'employee_id'):
                    s["employee_id"] = doc.parsed.employee_id
                if hasattr(doc.parsed, 'employee_name'):
                    s["employee_name"] = doc.parsed.employee_name
            summary.append(s)
        
        with open("data/documents_summary.json", 'w') as f:
            json.dump(summary, f, indent=2)
        print(f"  📁 Saved document summary to data/documents_summary.json")
    
    # ──────────────────────────────────────────────────────────────────────
    # PHASE 3: Build Reference Stores
    # ──────────────────────────────────────────────────────────────────────
    print("\n" + "─" * 50)
    print("📚 PHASE 3: Reference Stores")
    print("─" * 50)
    
    stores = build_reference_stores(documents)
    
    # ──────────────────────────────────────────────────────────────────────
    # PHASE 4: Run All Detectors
    # ──────────────────────────────────────────────────────────────────────
    print("\n" + "─" * 50)
    print("🔎 PHASE 4: Needle Detection")
    print("─" * 50)
    
    all_findings = []
    
    # Easy tier (40 pts max)
    easy_findings = run_easy_detectors(documents)
    all_findings.extend(easy_findings)
    
    # Medium tier (180 pts max)
    medium_findings = run_medium_detectors(documents, stores)
    all_findings.extend(medium_findings)
    
    # Evil tier (700 pts max)
    evil_findings = run_evil_detectors(documents, stores)
    all_findings.extend(evil_findings)
    
    # ──────────────────────────────────────────────────────────────────────
    # PHASE 5: Assembly & Output
    # ──────────────────────────────────────────────────────────────────────
    print("\n" + "─" * 50)
    print("📦 PHASE 5: Assembly")
    print("─" * 50)
    
    submission = assemble_submission(all_findings, team_id=args.team_id)
    save_submission(submission, args.output)
    
    # ──────────────────────────────────────────────────────────────────────
    # Summary
    # ──────────────────────────────────────────────────────────────────────
    elapsed = time.time() - start_time
    print("\n" + "=" * 70)
    print(f"✅ Pipeline complete in {elapsed:.1f}s")
    print(f"   Total findings: {len(submission['findings'])}")
    print(f"   Output: {args.output}")
    print("=" * 70)


if __name__ == "__main__":
    main()
