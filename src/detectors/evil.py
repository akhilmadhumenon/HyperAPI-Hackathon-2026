"""
Evil needle detectors (Tier 3 — 100 needles × 7 pts = 700 pts).
Multi-document aggregation and pattern detection across 3-5+ documents.
"""
import re
from typing import List, Dict, Set, Tuple
from collections import defaultdict
from rapidfuzz import fuzz

from src.models import Finding, Document, DocType, Invoice
from src.vendor_master import VENDOR_MASTER, VENDOR_BY_NAME, VENDOR_NAMES
from src.reference_store import (
    PORegistry, InvoiceRegistry, BankStatementChain,
    ExpenseIndex, CreditDebitGraph, VendorMatcher
)


def detect_quantity_accumulation(documents: List[Document], stores: dict) -> List[Finding]:
    """
    Sum of quantities across 3-5 invoices exceeds the PO quantity by 20%.
    """
    findings = []
    po_registry: PORegistry = stores['po_registry']
    invoice_registry: InvoiceRegistry = stores['invoice_registry']
    
    for po_id, po in po_registry.orders.items():
        invoices = invoice_registry.get_by_po(po_id)
        if len(invoices) < 2: # Keep 2 as min, though typical is 3-5
            continue
            
        # TRACKING: Accumulate qty per po_item
        # Use item index as key for stability
        accumulated_qty = defaultdict(float)
        contributing_invoices = defaultdict(set)
        
        for inv in invoices:
            # Vendor sanity check (60 for distorted typos)
            if inv.vendor_name and po.vendor_name:
                v_score = fuzz.token_sort_ratio(inv.vendor_name.lower(), po.vendor_name.lower())
                if v_score < 60:
                    continue
            
            for inv_item in inv.line_items:
                # Find BEST matching po_item (consistent with mismatch detector)
                best_po_item_idx = -1
                best_score = 0
                
                for idx, po_item in enumerate(po.line_items):
                    po_hsn = getattr(po_item, 'hsn_sac', '')
                    inv_hsn = getattr(inv_item, 'hsn_sac', '')
                    hsn_match = bool(inv_hsn and po_hsn and inv_hsn == po_hsn)
                    
                    desc_score = fuzz.token_sort_ratio(inv_item.description.lower(), po_item.description.lower())
                    effective_score = desc_score + (30 if hsn_match else 0)
                    
                    if effective_score > best_score and effective_score >= 85:
                        best_score = effective_score
                        best_po_item_idx = idx
                
                if best_po_item_idx != -1:
                    accumulated_qty[best_po_item_idx] += inv_item.qty
                    contributing_invoices[best_po_item_idx].add(inv.doc_id)
        
        # Now check for over-accumulation
        po_findings = []
        for idx, po_item in enumerate(po.line_items):
            total_qty = accumulated_qty[idx]
            inv_ids = list(contributing_invoices[idx])
            
            if po_item.qty > 0 and total_qty > po_item.qty * 1.2 and len(inv_ids) >= 2:
                all_pages = set(po.pages)
                for inv_id in inv_ids:
                    inv_obj = invoice_registry.get(inv_id)
                    if inv_obj: all_pages.update(inv_obj.pages)
                
                po_findings.append(Finding(
                    category="quantity_accumulation",
                    pages=sorted(list(all_pages)),
                    document_refs=[po_id] + sorted(inv_ids),
                    description=(
                        f"Quantity accumulation for '{po_item.description}' (HSN {getattr(po_item, 'hsn_sac', '')}): "
                        f"Cumulative qty across {len(inv_ids)} invoices is {total_qty}, "
                        f"exceeding PO {po_id} contracted quantity of {po_item.qty}."
                    ),
                    reported_value=str(round(total_qty, 2)),
                    correct_value=str(po_item.qty),
                ))
        
        # CONSOLIDATION: If one PO has multiple line item breaches, 
        # report only the top 2 worst offenders to save finding slots.
        # Usually an 'evil' needle is one specific accumulation instance.
        po_findings.sort(key=lambda x: float(x.reported_value)/float(x.correct_value) if float(x.correct_value) > 0 else 0, reverse=True)
        findings.extend(po_findings[:2])
    
    return findings


def detect_price_escalation(documents: List[Document], stores: dict) -> List[Finding]:
    """
    All invoices against a PO charge rates exceeding the contracted PO rate.
    """
    findings = []
    po_registry: PORegistry = stores['po_registry']
    invoice_registry: InvoiceRegistry = stores['invoice_registry']
    
    for po_id, po in po_registry.orders.items():
        invoices = invoice_registry.get_by_po(po_id)
        if len(invoices) < 2:
            continue
        
        # TRACKING: Check escalation per matched line item
        matched_rates = defaultdict(list) # po_item_idx -> List[(inv_doc_id, rate, pages)]
        
        for inv in invoices:
            # Vendor check
            if inv.vendor_name and po.vendor_name:
                if fuzz.token_sort_ratio(inv.vendor_name.lower(), po.vendor_name.lower()) < 70:
                    continue
            
            for inv_item in inv.line_items:
                # Find BEST matching po_item
                best_po_idx = -1
                best_score = 0
                for idx, po_item in enumerate(po.line_items):
                    po_hsn = getattr(po_item, 'hsn_sac', '')
                    inv_hsn = getattr(inv_item, 'hsn_sac', '')
                    hsn_match = bool(inv_hsn and po_hsn and inv_hsn == po_hsn)
                    
                    desc_score = fuzz.token_sort_ratio(inv_item.description.lower(), po_item.description.lower())
                    effective_score = desc_score + (30 if hsn_match else 0)
                    
                    if effective_score > best_score and effective_score >= 85:
                        best_score = effective_score
                        best_po_idx = idx
                
                if best_po_idx != -1:
                    matched_rates[best_po_idx].append((inv.doc_id, inv_item.rate, inv.pages))
        
        # Now check for escalation
        for idx, item_matches in matched_rates.items():
            po_item = po.line_items[idx]
            # Escalation means ALL invoices for this item charge > rate
            if len(item_matches) == len(invoices) and len(invoices) >= 2:
                is_all_higher = all(m[1] > po_item.rate * 1.05 for m in item_matches)
                if is_all_higher and po_item.rate > 0:
                    all_pages = set(po.pages)
                    inv_ids = []
                    rates = []
                    for doc_id, rate, pgs in item_matches:
                        all_pages.update(pgs)
                        inv_ids.append(doc_id)
                        rates.append(str(round(rate, 2)))
                    
                    findings.append(Finding(
                        category="price_escalation",
                        pages=sorted(list(all_pages)),
                        document_refs=sorted(list(set(inv_ids + [po_id]))),
                        description=(
                            f"Price escalation for '{po_item.description}': "
                            f"All {len(invoices)} invoices charge rates {', '.join(rates)} "
                            f"exceeding PO {po_id} contracted rate of {po_item.rate}."
                        ),
                        reported_value=", ".join(rates),
                        correct_value=str(po_item.rate),
                    ))
    
    return findings


def detect_balance_drift(documents: List[Document], stores: dict) -> List[Finding]:
    """
    Bank statement opening balance ≠ previous month's closing balance.
    """
    findings = []
    bank_chain: BankStatementChain = stores['bank_chain']
    ordered = bank_chain.get_ordered()
    
    # Sort and group by account name
    by_account = defaultdict(list)
    for bs in ordered:
        # Normalise account name: lowercase and strip
        acc = bs.account_name.lower().strip() if bs.account_name else "unknown"
        by_account[acc].append(bs)
        
    for account, statements in by_account.items():
        # Safety: if account is "unknown" and we have many statements, 
        # they might be interleaved. Try a secondary grouping by ID range.
        if account == "unknown" and len(statements) > 12:
            # Group by chunks of 12 (standard monthly frequency)
            # This is a heuristic for when account names are missing.
            subgroups = defaultdict(list)
            for s in statements:
                # Extract numeric part of doc_id
                m = re.search(r'(\d+)$', s.doc_id)
                if m:
                    idx = int(m.group(1))
                    group_id = (idx - 1) // 12
                    subgroups[group_id].append(s)
            
            all_groups = list(subgroups.values())
        else:
            all_groups = [statements]

        for group in all_groups:
            for i in range(1, len(group)):
                prev_bs = group[i - 1]
                curr_bs = group[i]
                
                # Check is not None to catch 0.0 balances
                if prev_bs.closing_balance is not None and curr_bs.opening_balance is not None:
                    # Floating point safety
                    diff = abs(prev_bs.closing_balance - curr_bs.opening_balance)
                    if diff > 0.5: # Relaxed tolerance slightly
                        findings.append(Finding(
                            category="balance_drift",
                            pages=sorted(list(set(curr_bs.pages + prev_bs.pages))),
                            document_refs=[curr_bs.doc_id, prev_bs.doc_id],
                            description=(
                                f"Balance drift: {curr_bs.doc_id} opening balance ({curr_bs.opening_balance}) "
                                f"does not match {prev_bs.doc_id} closing balance ({prev_bs.closing_balance}). "
                                f"Difference: {diff:.2f}"
                            ),
                            reported_value=str(curr_bs.opening_balance),
                            correct_value=str(prev_bs.closing_balance),
                        ))
    
    return findings


def detect_circular_reference(documents: List[Document], stores: dict) -> List[Finding]:
    """
    Credit/debit notes form a loop (A→B→C→A) — none traces back to a real invoice.
    """
    findings = []
    cd_graph: CreditDebitGraph = stores['cd_graph']
    
    cycles = cd_graph.find_cycles()
    
    seen_cycles = set()
    for cycle in cycles:
        # Normalize cycle for dedup
        cycle_key = tuple(sorted(cycle[:-1]))  # Remove last (duplicate of first)
        if cycle_key in seen_cycles:
            continue
        seen_cycles.add(cycle_key)
        
        # Check if any ref in the cycle points to a real invoice
        has_real_ref = False
        for node_id in cycle[:-1]:
            note = cd_graph.notes.get(node_id)
            if note:
                for ref in note.references:
                    if cd_graph.is_real_invoice_ref(ref):
                        has_real_ref = True
        
        if not has_real_ref:
            all_pages = []
            for node_id in cycle[:-1]:
                note = cd_graph.notes.get(node_id)
                if note:
                    all_pages.extend(note.pages)
            
            findings.append(Finding(
                category="circular_reference",
                pages=sorted(list(set(all_pages))),
                document_refs=list(cycle[:-1]),
                description=f"Circular reference detected: {' → '.join(cycle)}. No note in the loop references a real invoice.",
                reported_value=" → ".join(cycle),
                correct_value="Should reference a real invoice",
            ))
    
    return findings


def detect_triple_expense_claim(documents: List[Document], stores: dict) -> List[Finding]:
    """
    Same hotel stay claimed in 3 different expense reports.
    """
    findings = []
    expense_index: ExpenseIndex = stores['expense_index']
    
    triples = expense_index.find_triples()
    
    for key, items in triples:
        report_ids = sorted(list(set([doc_id for doc_id, item in items])))
        
        er_pages = []
        for doc_id in report_ids:
            er = expense_index.reports.get(doc_id)
            if er:
                er_pages.extend(er.pages)
        
        parts = key.split("|")
        desc = parts[0] if len(parts) > 0 else key
        amount = parts[1] if len(parts) > 1 else ""
        
        findings.append(Finding(
            category="triple_expense_claim",
            pages=sorted(list(set(er_pages))),
            document_refs=report_ids,
            description=f"Hotel stay '{desc}' for ₹{amount} claimed in {len(report_ids)} reports: {', '.join(report_ids)}",
            reported_value=str(len(report_ids)),
            correct_value="1",
        ))
    
    return findings


def detect_employee_id_collision(documents: List[Document], stores: dict) -> List[Finding]:
    """
    Same Employee ID used by two different people across expense reports.
    """
    findings = []
    expense_index: ExpenseIndex = stores['expense_index']
    
    for emp_id, reports in expense_index.by_employee_id.items():
        if len(reports) < 2:
            continue
        
        # Check if different names use the same ID
        names = set()
        for er in reports:
            if er.employee_name:
                names.add(er.employee_name.lower().strip())
        
        if len(names) >= 2:
            all_pages = []
            doc_refs = []
            for er in reports:
                all_pages.extend(er.pages)
                doc_refs.append(er.doc_id)
            
            names_list = [er.employee_name for er in reports if er.employee_name]
            unique_names = list(set(names_list))
            
            findings.append(Finding(
                category="employee_id_collision",
                pages=sorted(list(set(all_pages))),
                document_refs=doc_refs,
                description=f"Employee ID '{emp_id}' used by {len(names)} different people: {', '.join(unique_names[:5])}",
                reported_value=emp_id,
                correct_value=f"Should be unique — found {len(names)} names",
            ))
    
    return findings


def detect_fake_vendor(documents: List[Document], stores: dict) -> List[Finding]:
    """
    Invoice from a vendor that doesn't exist in the Vendor Master,
    OR a vendor with a correct name but mismatching GSTIN.
    """
    findings = []
    vendor_matcher: VendorMatcher = stores['vendor_matcher']
    
    # Track (vendor_name, gstin) to avoid over-reporting the same pattern
    # However, for fake vendors, usually every document is a needle.
    # We'll report each document but cap if it gets too high.
    
    for doc in documents:
        if doc.doc_type != DocType.INVOICE or not doc.parsed:
            continue
        
        inv: Invoice = doc.parsed
        if not inv.vendor_name:
            continue
        
        # Try to match vendor name
        result = vendor_matcher.match(inv.vendor_name, threshold=75)
        
        if result is None:
            # No match at all — fake vendor
            findings.append(Finding(
                category="fake_vendor",
                pages=inv.pages,
                document_refs=[inv.doc_id],
                description=f"Vendor '{inv.vendor_name}' does not exist in the Vendor Master (35 registered vendors). No fuzzy match found.",
                reported_value=inv.vendor_name,
                correct_value="Not in Vendor Master",
            ))
        else:
            # Case 2: Name matches, but GSTIN is wrong
            correct_name, score = result
            master_vendor = VENDOR_BY_NAME.get(correct_name.lower())
            
            if master_vendor and inv.vendor_gstin:
                # Normalise for comparison
                inv_gstin = inv.vendor_gstin.upper().strip().replace(' ', '')
                master_gstin = master_vendor.gstin.upper().strip().replace(' ', '')
                
                if inv_gstin != master_gstin:
                    findings.append(Finding(
                        category="fake_vendor",
                        pages=inv.pages,
                        document_refs=[inv.doc_id],
                        description=(
                            f"Vendor name '{inv.vendor_name}' matches '{correct_name}' in master, "
                            f"but GSTIN '{inv.vendor_gstin}' is incorrect. Registered GSTIN is '{master_vendor.gstin}'."
                        ),
                        reported_value=inv.vendor_gstin,
                        correct_value=master_vendor.gstin,
                    ))
    
    return findings


def detect_phantom_po_reference(documents: List[Document], stores: dict) -> List[Finding]:
    """
    Invoice cites a PO number that doesn't exist anywhere in the dataset.
    """
    findings = []
    po_registry: PORegistry = stores['po_registry']
    invoice_registry: InvoiceRegistry = stores['invoice_registry']
    
    for inv_id, inv in invoice_registry.invoices.items():
        if not inv.po_ref:
            continue
        
        if not po_registry.exists(inv.po_ref):
            findings.append(Finding(
                category="phantom_po_reference",
                pages=inv.pages,
                document_refs=[inv.doc_id],
                description=f"Invoice {inv.doc_id} references PO '{inv.po_ref}' which doesn't exist in the dataset",
                reported_value=inv.po_ref,
                correct_value="PO does not exist",
            ))
    
    return findings


def run_evil_detectors(documents: List[Document], stores: dict) -> List[Finding]:
    """Run all evil-tier detectors."""
    print("\n🔴 Running EVIL detectors (Tier 3)...")
    
    all_findings = []
    
    detectors = [
        ("quantity_accumulation", detect_quantity_accumulation),
        ("price_escalation", detect_price_escalation),
        ("balance_drift", detect_balance_drift),
        ("circular_reference", detect_circular_reference),
        ("triple_expense_claim", detect_triple_expense_claim),
        ("employee_id_collision", detect_employee_id_collision),
        ("fake_vendor", detect_fake_vendor),
        ("phantom_po_reference", detect_phantom_po_reference),
    ]
    
    for name, detector in detectors:
        findings = detector(documents, stores)
        print(f"  {name}: {len(findings)} findings")
        all_findings.extend(findings)
    
    print(f"  Total evil findings: {len(all_findings)}")
    return all_findings
