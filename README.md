# Financial Gauntlet — Fraud Detection Pipeline

**Team:** `baby_sharks` | **HyperAPI Hackathon 2026**

A fully deterministic, zero-API fraud detection pipeline that ingests a 1000-page financial PDF (`gauntlet.pdf`), structures it into a SQLite database, and detects 20 categories of financial inconsistencies ("needles") via SQL queries.

**Final result: 187 findings, confirmed score ~899 pts (100% DB validation pass rate).**

---

## How It Works

```
gauntlet.pdf
    │
    ▼
custom_split.py       — Deterministic page segmentation via pypdf header detection
    │
    ▼  segments.json
    │
    ▼
db_setup.py           — Initialize SQLite schema (11 normalized tables)
    │
    ▼
ingestion_engine.py   — Parse each segment's text → insert into DB
    │
    ▼  financial_gauntlet.db
    │
    ▼
validation_engine.py  — 20 SQL-based fraud detectors
    │
    ▼
findings_builder.py   — Deduplicate, sort, format → submission JSON
    │
    ▼  output/submission.json
```

No LLMs, no external APIs — everything runs locally in seconds.

---

## Quickstart

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Place gauntlet.pdf in this directory

# 3. Run the full pipeline
python main.py

# Outputs: output/submission.json
```

### Partial runs

```bash
python main.py --split-only      # Re-run PDF segmentation only
python main.py --ingest-only     # Re-run ingestion (uses existing segments.json)
python main.py --validate-only   # Re-run fraud detection (uses existing DB)
python main.py --stats           # Print DB row counts
```

---

## File Overview

| File | Purpose |
|---|---|
| `main.py` | Pipeline orchestrator — runs all 4 steps in order |
| `custom_split.py` | Splits `gauntlet.pdf` into document segments by detecting page headers |
| `db_setup.py` | Creates the SQLite schema (invoices, POs, bank statements, expenses, etc.) |
| `ingestion_engine.py` | Parses each document segment's text and inserts normalized rows into DB |
| `validation_engine.py` | Runs all 20 fraud detection checks as SQL queries against the DB |
| `findings_builder.py` | Deduplicates raw findings and formats them into the submission JSON |
| `utils.py` | Shared helpers: amount/date parsing, GSTIN validation, vendor fuzzy matching |
| `validate_submission.py` | Post-hoc validator — cross-checks submission JSON against DB and PDF |
| `merge_submissions.py` | Utility to merge two submission files (used for iterative improvement) |

---

## Needle Categories

### Evil (7 pts each)
| Category | Detection Method |
|---|---|
| `quantity_accumulation` | `SUM(inv_qty) > 1.2 × PO_qty` across all invoices for a PO |
| `price_escalation` | `MAX(rate) > MIN(rate) × 1.5` for same item across invoices |
| `balance_drift` | Closing balance of statement N ≠ Opening balance of statement N+1 |
| `circular_reference` | CN/DN chains that form a cycle (DFS graph traversal) |
| `triple_expense_claim` | Same hotel/expense billed in 3+ separate expense reports |
| `employee_id_collision` | Two employees share the same employee ID |
| `fake_vendor` | Invoice vendor GSTIN absent from the Vendor Master |
| `phantom_po_reference` | Invoice references a PO number that doesn't exist in DB |

### Medium (3 pts each)
| Category | Detection Method |
|---|---|
| `po_invoice_mismatch` | Invoice rate or quantity doesn't match the linked PO line item |
| `vendor_name_typo` | Invoice vendor name ≥ 70% similar to master but not identical |
| `double_payment` | Same amount paid to same vendor twice via bank transactions |
| `ifsc_mismatch` | Invoice IFSC differs from Vendor Master IFSC |
| `duplicate_expense` | Identical expense entry appears in multiple reports |
| `date_cascade` | Invoice date is earlier than the PO date it references |
| `gstin_state_mismatch` | GSTIN state prefix doesn't match vendor's registered state |

### Easy (1 pt each)
| Category | Detection Method |
|---|---|
| `arithmetic_error` | `SUM(line_items) ≠ subtotal` or `subtotal + tax ≠ grand_total` |
| `billing_typo` | `qty × rate ≠ amount` on a line item |
| `duplicate_line_item` | Same description appears twice on the same invoice |
| `invalid_date` | Date field contains an impossible calendar date (e.g. Feb 30) |
| `wrong_tax_rate` | Applied GST rate deviates >10% of subtotal from the HSN-mandated rate |

---

## Submission Format

```json
{
  "team_id": "baby_sharks",
  "findings": [
    {
      "finding_id": "F-001",
      "category": "fake_vendor",
      "pages": [142],
      "document_refs": ["INV-2025-00087"],
      "description": "Vendor GSTIN 29ABCDE1234F1Z5 not found in Vendor Master",
      "reported_value": "29ABCDE1234F1Z5",
      "correct_value": "GSTIN absent from master"
    }
  ]
}
```

---

## Validation

`validate_submission.py` performs two-level verification on any submission file:

- **Level 1 (DB check):** Re-runs category-specific SQL against the database to confirm reported/correct values match
- **Level 2 (PDF check):** Extracts text from cited pages to confirm document refs appear there

```bash
# Validate our submission
python validate_submission.py

# Validate a specific category
python validate_submission.py --category fake_vendor

# Validate a specific finding
python validate_submission.py --finding F-042

# DB check only (no PDF scan)
python validate_submission.py --db-only

# Verbose output
python validate_submission.py --verbose
```

---

## Design Decisions

- **No API calls in the core pipeline.** All extraction uses `pypdf` text extraction + regex. This makes the pipeline fast, deterministic, and offline.
- **Precision over recall.** Every validation check has conservative thresholds to minimize false positives (−0.5 penalty each).
- **SQL-first fraud detection.** All 20 checks are implemented as SQL queries against normalized tables — not Python loops over raw text.
- **Idempotent ingestion.** Re-running `main.py` clears and repopulates tables, so the pipeline can be run fresh at any time.
