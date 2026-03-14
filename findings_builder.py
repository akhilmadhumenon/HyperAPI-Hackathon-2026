"""
Findings builder: converts raw validation findings into the hackathon submission JSON.
"""
import json
import os

OUTPUT_DIR = os.path.join(os.path.dirname(__file__), 'output')
SUBMISSION_PATH = os.path.join(OUTPUT_DIR, 'submission.json')

TEAM_ID = "baby_sharks"


def build_submission(raw_findings: list[dict]) -> dict:
    """
    Format findings into the submission schema:
    {
      "team_id": "baby_sharks",
      "findings": [
        {
          "finding_id": "F-001",
          "category": "...",
          "pages": [...],
          "document_refs": [...],
          "description": "...",
          "reported_value": "...",
          "correct_value": "..."
        }
      ]
    }
    Rules:
    - Deduplicate: same category + overlapping document_refs → keep first (highest-confidence)
    - Sort: evil first (most valuable), then medium, then easy
    """
    # Difficulty weights for sorting
    WEIGHTS = {
        'quantity_accumulation': 7, 'price_escalation': 7, 'balance_drift': 7,
        'circular_reference': 7, 'triple_expense_claim': 7, 'employee_id_collision': 7,
        'fake_vendor': 7, 'phantom_po_reference': 7,
        'po_invoice_mismatch': 3, 'vendor_name_typo': 3, 'double_payment': 3,
        'ifsc_mismatch': 3, 'duplicate_expense': 3, 'date_cascade': 3,
        'gstin_state_mismatch': 3,
        'arithmetic_error': 1, 'billing_typo': 1, 'duplicate_line_item': 1,
        'invalid_date': 1, 'wrong_tax_rate': 1,
    }

    # Sort by weight descending
    sorted_findings = sorted(
        raw_findings,
        key=lambda f: WEIGHTS.get(f.get('category', ''), 0),
        reverse=True,
    )

    # Deduplicate: within same category, skip if document_refs overlap >= 50% with existing
    # Exception: balance_drift findings are per consecutive pair — only deduplicate exact duplicates
    EXACT_DEDUP_CATEGORIES = {'balance_drift'}

    final: list[dict] = []
    seen: dict[str, list[set]] = {}  # category → list of doc_ref sets already reported

    for f in sorted_findings:
        cat = f.get('category', '')
        doc_refs = set(f.get('document_refs', []))

        duplicate = False
        for existing_refs in seen.get(cat, []):
            if not doc_refs or not existing_refs:
                continue
            if cat in EXACT_DEDUP_CATEGORIES:
                # Only deduplicate if EXACT same set of refs
                if doc_refs == existing_refs:
                    duplicate = True
                    break
            else:
                overlap = len(doc_refs & existing_refs) / max(len(doc_refs), len(existing_refs))
                if overlap >= 0.50:
                    duplicate = True
                    break

        if not duplicate:
            seen.setdefault(cat, []).append(doc_refs)
            final.append(f)

    # Assign finding IDs
    formatted = []
    for idx, f in enumerate(final, start=1):
        formatted.append({
            'finding_id': f'F-{idx:03d}',
            'category':        f.get('category', ''),
            'pages':           sorted(set(f.get('pages', []))),
            'document_refs':   f.get('document_refs', []),
            'description':     f.get('description', ''),
            'reported_value':  str(f.get('reported_value', '')),
            'correct_value':   str(f.get('correct_value', '')),
        })

    return {'team_id': TEAM_ID, 'findings': formatted}


def save_submission(submission: dict, path: str = SUBMISSION_PATH) -> str:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w') as f:
        json.dump(submission, f, indent=2)
    return path


def print_summary(submission: dict):
    findings = submission['findings']
    WEIGHTS = {
        'quantity_accumulation': 7, 'price_escalation': 7, 'balance_drift': 7,
        'circular_reference': 7, 'triple_expense_claim': 7, 'employee_id_collision': 7,
        'fake_vendor': 7, 'phantom_po_reference': 7,
        'po_invoice_mismatch': 3, 'vendor_name_typo': 3, 'double_payment': 3,
        'ifsc_mismatch': 3, 'duplicate_expense': 3, 'date_cascade': 3,
        'gstin_state_mismatch': 3,
        'arithmetic_error': 1, 'billing_typo': 1, 'duplicate_line_item': 1,
        'invalid_date': 1, 'wrong_tax_rate': 1,
    }
    from collections import Counter
    cat_counts = Counter(f['category'] for f in findings)
    total_score = sum(WEIGHTS.get(f['category'], 0) for f in findings)

    print(f"\n{'='*55}")
    print(f"  SUBMISSION SUMMARY — {submission['team_id']}")
    print(f"{'='*55}")
    print(f"  Total findings: {len(findings)}")
    print(f"  Max possible score (if all correct): {total_score}")
    print(f"\n  By category:")
    for cat, count in sorted(cat_counts.items(), key=lambda x: -WEIGHTS.get(x[0], 0)):
        w = WEIGHTS.get(cat, 0)
        print(f"    [{w}pt]  {cat}: {count}")
    print(f"{'='*55}")


if __name__ == '__main__':
    # Test with dummy findings
    test = [{'category': 'fake_vendor', 'pages': [100], 'document_refs': ['INV-X'],
              'description': 'test', 'reported_value': 'X', 'correct_value': 'Y'}]
    sub = build_submission(test)
    print(json.dumps(sub, indent=2))
