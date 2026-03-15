"""
Assemble findings into final submission JSON.
Handles deduplication, confidence filtering, and ID assignment.
"""
import json
from typing import List, Dict
from collections import defaultdict
from src.models import Finding


def deduplicate_findings(findings: List[Finding]) -> List[Finding]:
    """Remove duplicate findings and enforce category caps to reduce false positives."""
    seen = set()
    final_findings = []
    
    # Priority Categories (Evil first)
    evil_cats = {"quantity_accumulation", "price_escalation", "balance_drift", "circular_reference", "triple_expense_claim", "employee_id_collision", "fake_vendor", "phantom_po_reference"}
    
    # Sort findings: Evil categories first
    findings.sort(key=lambda x: 2 if x.category in evil_cats else 1, reverse=True)
    
    cat_counts = defaultdict(int)
    for f in findings:
        # Create dedup key
        key = (f.category, tuple(sorted(f.document_refs)), f.reported_value)
        if key in seen:
            continue
            
        # CATEGORY CAP: Max 30 findings per category (Safety Valve)
        # Goal is 200 total; 20 categories * 10 = 200. 30 allows for some noisy categories.
        # CATEGORY CAPS: Balance findings to stay near ~200 total
        # We tighten noisy categories to reduce false positive impact
        cap = 40
        if f.category in ["date_cascade", "wrong_tax_rate"]:
            cap = 15
        elif f.category == "po_invoice_mismatch":
            cap = 20
            
        if cat_counts[f.category] >= cap:
            continue
            
        # CROSS-CATEGORY SUPPRESSION
        # If we already reported price_escalation/accumulation for these docs, skip po_invoice_mismatch
        if f.category == "po_invoice_mismatch":
            has_evil = any(
                ef.category in ["price_escalation", "quantity_accumulation"] and 
                any(r in ef.document_refs for r in f.document_refs)
                for ef in final_findings
            )
            if has_evil:
                continue

        seen.add(key)
        cat_counts[f.category] += 1
        final_findings.append(f)
    
    print(f"  Dedup & Filter: {len(findings)} → {len(final_findings)} findings")
    return final_findings


def filter_by_confidence(findings: List[Finding], min_confidence: float = 0.5) -> List[Finding]:
    """Filter out low-confidence findings to reduce false positives."""
    filtered = [f for f in findings if f.confidence >= min_confidence]
    print(f"  Confidence filter: {len(findings)} → {len(filtered)} findings (threshold: {min_confidence})")
    return filtered


def assign_ids(findings: List[Finding]) -> List[Finding]:
    """Assign sequential finding IDs."""
    for i, f in enumerate(findings, 1):
        f.finding_id = f"F-{i:03d}"
    return findings


def assemble_submission(findings: List[Finding], team_id: str = "team_42") -> Dict:
    """Assemble the final submission JSON."""
    print("\n📦 Assembling submission...")
    
    # Deduplicate
    findings = deduplicate_findings(findings)
    
    # Assign IDs
    findings = assign_ids(findings)
    
    # Build submission
    submission = {
        "team_id": team_id,
        "findings": [
            {
                "finding_id": f.finding_id,
                "category": f.category,
                "pages": f.pages,
                "document_refs": f.document_refs,
                "description": f.description,
                "reported_value": f.reported_value,
                "correct_value": f.correct_value,
            }
            for f in findings
        ]
    }
    
    # Print summary
    cat_counts = defaultdict(int)
    for f in findings:
        cat_counts[f.category] += 1
    
    print(f"\n📊 Submission Summary:")
    print(f"  Team ID: {team_id}")
    print(f"  Total findings: {len(findings)}")
    print(f"  By category:")
    
    # Difficulty tiers
    easy_cats = {"arithmetic_error", "billing_typo", "duplicate_line_item", "invalid_date", "wrong_tax_rate"}
    medium_cats = {"po_invoice_mismatch", "vendor_name_typo", "double_payment", "ifsc_mismatch", "duplicate_expense", "date_cascade", "gstin_state_mismatch"}
    evil_cats = {"quantity_accumulation", "price_escalation", "balance_drift", "circular_reference", "triple_expense_claim", "employee_id_collision", "fake_vendor", "phantom_po_reference"}
    
    easy_count = sum(cat_counts[c] for c in easy_cats)
    medium_count = sum(cat_counts[c] for c in medium_cats)
    evil_count = sum(cat_counts[c] for c in evil_cats)
    
    print(f"\n  🟢 Easy ({easy_count} findings, {easy_count} max pts):")
    for cat in sorted(easy_cats):
        if cat_counts[cat] > 0:
            print(f"    {cat}: {cat_counts[cat]}")
    
    print(f"\n  🟡 Medium ({medium_count} findings, {medium_count * 3} max pts):")
    for cat in sorted(medium_cats):
        if cat_counts[cat] > 0:
            print(f"    {cat}: {cat_counts[cat]}")
    
    print(f"\n  🔴 Evil ({evil_count} findings, {evil_count * 7} max pts):")
    for cat in sorted(evil_cats):
        if cat_counts[cat] > 0:
            print(f"    {cat}: {cat_counts[cat]}")
    
    max_pts = easy_count * 1 + medium_count * 3 + evil_count * 7
    print(f"\n  Max possible score: {max_pts} / 920 pts")
    
    return submission


def save_submission(submission: Dict, output_path: str):
    """Save submission JSON to file."""
    with open(output_path, 'w') as f:
        json.dump(submission, f, indent=2)
    print(f"\n💾 Saved submission to: {output_path}")
