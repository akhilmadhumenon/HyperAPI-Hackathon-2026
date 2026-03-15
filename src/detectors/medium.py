"""
Medium needle detectors (Tier 2 — 60 needles × 3 pts = 180 pts).
Two-document cross-reference checks.
"""
import re
from typing import List, Dict, Optional, Tuple
from collections import defaultdict
from rapidfuzz import fuzz

from src.models import Finding, Document, DocType, Invoice, LineItem, POLineItem
from src.vendor_master import (
    VENDOR_MASTER, VENDOR_BY_NAME, VENDOR_BY_GSTIN,
    VENDOR_BY_IFSC, get_state_from_gstin
)
from src.reference_store import (
    PORegistry, InvoiceRegistry, BankStatementChain,
    ExpenseIndex, VendorMatcher
)
from src.utils import parse_date_to_tuple, normalize_state


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------





def _normalise_ref(ref: str) -> str:
    """Strip all non-alphanumeric characters and lowercase — for payment ref matching."""
    return re.sub(r'[^a-z0-9]', '', ref.lower())


def _vendor_scores_as_fake(vendor_name: str, vendor_matcher: VendorMatcher) -> bool:
    """
    Return True if the vendor name does NOT match any entry in the Vendor Master
    at all (i.e. it looks like a fake vendor rather than a typo of a real one).
    Uses a high confidence threshold — only exclude clear non-matches.
    """
    if vendor_matcher.is_exact_match(vendor_name):
        return False
    result = vendor_matcher.match(vendor_name, threshold=50)
    # If nothing matches even loosely, it's likely fake — leave it for fake_vendor detector
    return result is None


# ---------------------------------------------------------------------------
# match_po_item — unchanged, works correctly
# ---------------------------------------------------------------------------

def match_po_item(inv_item: LineItem, po_items: List[POLineItem]) -> Optional[POLineItem]:
    """Match an invoice line item to a PO line item using description and HSN."""
    best_item = None
    best_score = 0

    inv_desc = inv_item.description.lower().strip()
    inv_hsn = getattr(inv_item, 'hsn_sac', '')

    for po_item in po_items:
        po_desc = po_item.description.lower().strip()
        po_hsn = getattr(po_item, 'hsn_sac', '')

        hsn_match = bool(inv_hsn and po_hsn and inv_hsn == po_hsn)
        desc_score = fuzz.token_sort_ratio(inv_desc, po_desc)

        effective_score = desc_score + (30 if hsn_match else 0)

        if effective_score > best_score and effective_score >= 85:
            best_score = effective_score
            best_item = po_item

    return best_item


# ---------------------------------------------------------------------------
# Detectors
# ---------------------------------------------------------------------------

def detect_po_invoice_mismatch(documents: List[Document], stores: dict) -> List[Finding]:
    """
    Invoice qty or rate differs from the linked Purchase Order.

    Fixes vs original:
    1. Each (invoice, line) pair produces AT MOST ONE finding — rate mismatch
       takes priority over qty mismatch (a wrong rate is always worse).
    2. Rate comparison uses a 1% relative tolerance to avoid flagging rounding
       differences from currency conversion or GST-inclusive pricing.
    3. Qty check remains directional (over-billing only) — partial invoices are
       legitimate, so under-qty is not a needle.
    """
    findings = []
    po_registry: PORegistry = stores['po_registry']
    invoice_registry: InvoiceRegistry = stores['invoice_registry']

    for inv_id, inv in invoice_registry.invoices.items():
        if not inv.po_ref:
            continue

        po = po_registry.get(inv.po_ref)
        if not po:
            continue

        # Vendor sanity check
        if inv.vendor_name and po.vendor_name:
            v_score = fuzz.token_sort_ratio(inv.vendor_name.lower(), po.vendor_name.lower())
            if v_score < 70:
                continue

        for inv_item in inv.line_items:
            po_item = match_po_item(inv_item, po.line_items)
            if not po_item:
                continue

            # --- Rate mismatch (takes priority) ---
            # Use 1% relative tolerance to filter out rounding noise.
            if inv_item.rate > 0 and po_item.rate > 0:
                relative_diff = abs(inv_item.rate - po_item.rate) / po_item.rate
                if relative_diff > 0.01:
                    findings.append(Finding(
                        category="po_invoice_mismatch",
                        pages=sorted(list(set(inv.pages + po.pages))),
                        document_refs=[inv.doc_id, inv.po_ref],
                        description=(
                            f"Invoice {inv.doc_id} line '{inv_item.description}': "
                            f"rate={inv_item.rate} but PO {inv.po_ref} specifies rate={po_item.rate}"
                        ),
                        reported_value=str(inv_item.rate),
                        correct_value=str(po_item.rate),
                    ))
                    continue  # Rate already flagged — skip qty check for this line

            # --- Qty mismatch (only if rate was fine) ---
            # Only flag over-billing; under-billing is normal for partial invoices.
            if inv_item.qty > 0 and po_item.qty > 0:
                if inv_item.qty > po_item.qty + 0.1:
                    # SUPPRESSION: If this PO is already a quantity_accumulation needle, 
                    # don't flag individual invoices for quantity mismatch.
                    invoices = invoice_registry.get_by_po(inv.po_ref)
                    total_inv_qty = 0
                    for other_inv in invoices:
                        for other_item in other_inv.line_items:
                            if match_po_item(other_item, [po_item]):
                                total_inv_qty += other_item.qty
                    
                    if total_inv_qty > po_item.qty * 1.2 and len(invoices) >= 2:
                        continue # Skip — let quantity_accumulation handle it
                        
                    findings.append(Finding(
                        category="po_invoice_mismatch",
                        pages=sorted(list(set(inv.pages + po.pages))),
                        document_refs=[inv.doc_id, inv.po_ref],
                        description=(
                            f"Invoice {inv.doc_id} line '{inv_item.description}': "
                            f"qty={inv_item.qty} but PO {inv.po_ref} specifies qty={po_item.qty}"
                        ),
                        reported_value=str(inv_item.qty),
                        correct_value=str(po_item.qty),
                    ))

    return findings


def detect_vendor_name_typo(documents: List[Document], stores: dict) -> List[Finding]:
    """
    Vendor name on invoice is misspelled vs the Vendor Master.

    Fix vs original:
    - Guard against fake vendors that happen to fuzzy-match a real name.
      If a vendor name matches nothing in the master above the 50% floor,
      it is left for detect_fake_vendor to handle rather than flagged here.
    - Deduplicate: report each vendor name string only once (multiple invoices
      from the same misspelled vendor should not multiply the finding count).
    """
    findings = []
    vendor_matcher: VendorMatcher = stores['vendor_matcher']
    seen_typos: set = set()  # (misspelled_name, correct_name)

    for doc in documents:
        if doc.doc_type != DocType.INVOICE or not doc.parsed:
            continue

        inv: Invoice = doc.parsed
        if not inv.vendor_name:
            continue

        if vendor_matcher.is_exact_match(inv.vendor_name):
            continue

        # If nothing matches even loosely → likely fake, not a typo
        result = vendor_matcher.match(inv.vendor_name, threshold=60)
        if not result:
            continue

        correct_name, score = result

        # score == 100 means exact match found via alternate path — skip
        if score >= 100:
            continue

        key = (inv.vendor_name.lower().strip(), correct_name.lower().strip())
        if key in seen_typos:
            continue
        seen_typos.add(key)

        findings.append(Finding(
            category="vendor_name_typo",
            pages=inv.pages,
            document_refs=[inv.doc_id],
            description=(
                f"Vendor name '{inv.vendor_name}' appears to be misspelled. "
                f"Closest match in Vendor Master: '{correct_name}' (similarity: {score:.2f}%)"
            ),
            reported_value=inv.vendor_name,
            correct_value=correct_name,
        ))

    return findings


def detect_double_payment(documents: List[Document], stores: dict) -> List[Finding]:
    """
    Same payment appears in two different bank statements months apart.

    Fixes vs original:
    1. Payment reference normalisation — strip all non-alphanumeric characters
       before building the dedup key. Catches refs that differ only by
       punctuation (e.g. 'RTGS-542883' vs 'RTGS542883').
    2. Invoice duplicate branch: normalise dates to parsed tuples so that
       format inconsistencies ('03/05/2025' vs '3 May 2025') don't cause
       genuine duplicates to miss each other.
    3. Avoid double-counting: if the same pair of bank statements is matched
       by both amount+ref AND by invoice, emit only one finding.
    """
    findings = []
    bank_chain: BankStatementChain = stores['bank_chain']
    invoice_registry: InvoiceRegistry = stores['invoice_registry']
    emitted_pairs: set = set()

    # 1. Bank statement duplicates
    payment_index: Dict = defaultdict(list)
    for bs in bank_chain.statements:
        for txn in bs.transactions:
            if txn.debit > 0:
                raw_ref = txn.payment_ref or txn.vendor_ref or ""
                if not raw_ref:
                    continue
                norm_ref = _normalise_ref(raw_ref)
                key = (round(txn.debit, 2), norm_ref)
                payment_index[key].append((bs, txn))

    for key, instances in payment_index.items():
        if len(instances) < 2:
            continue

        bs_ids = sorted(set(bs.doc_id for bs, _ in instances))
        if len(bs_ids) < 2:
            continue

        pair_key = tuple(bs_ids)
        if pair_key in emitted_pairs:
            continue
        emitted_pairs.add(pair_key)

        pages = sorted(set(p for bs, _ in instances for p in bs.pages))
        months = [f"{bs.month}_{bs.year}" for bs, _ in instances] # Use identifiable month label

        findings.append(Finding(
            category="double_payment",
            pages=pages,
            document_refs=bs_ids,
            description=(
                f"Payment of amount {key[0]} with ref '{key[1]}' appears in "
                f"{len(bs_ids)} bank statements across: {', '.join(months)}"
            ),
            reported_value=str(key[0]),
            correct_value=str(key[0]),
        ))

    # 2. Invoice duplicates (same vendor + amount + date submitted twice)
    invoice_index: Dict = defaultdict(list)
    for inv in invoice_registry.invoices.values():
        if not (inv.grand_total > 0 and inv.vendor_name):
            continue
        parsed_date = parse_date_to_tuple(inv.date) if inv.date else None
        key = (inv.vendor_name.lower().strip(), round(inv.grand_total, 2), parsed_date)
        invoice_index[key].append(inv)

    for key, invs in invoice_index.items():
        if len(invs) < 2:
            continue

        inv_ids = sorted(set(inv.doc_id for inv in invs))
        if len(inv_ids) < 2:
            continue

        pair_key = tuple(inv_ids)
        if pair_key in emitted_pairs:
            continue
        emitted_pairs.add(pair_key)

        pages = sorted(set(p for inv in invs for p in inv.pages))
        findings.append(Finding(
            category="double_payment",
            pages=pages,
            document_refs=inv_ids,
            description=(
                f"Duplicate invoice: {', '.join(inv_ids)} share the same vendor, "
                f"amount (₹{key[1]}), and date"
            ),
            reported_value=str(len(invs)),
            correct_value="1",
        ))

    return findings


def detect_ifsc_mismatch(documents: List[Document], stores: dict) -> List[Finding]:
    """
    Bank IFSC on invoice doesn't match vendor's registered IFSC in Vendor Master.
    Unchanged in logic — was working correctly.
    """
    findings = []
    vendor_matcher: VendorMatcher = stores['vendor_matcher']

    for doc in documents:
        if doc.doc_type != DocType.INVOICE or not doc.parsed:
            continue

        inv: Invoice = doc.parsed
        if not inv.vendor_ifsc or not inv.vendor_name:
            continue

        match_result = vendor_matcher.match(inv.vendor_name, threshold=70)
        if not match_result:
            continue

        correct_name, score = match_result
        vendor = VENDOR_BY_NAME.get(correct_name.lower())
        if vendor and vendor.ifsc != inv.vendor_ifsc:
            findings.append(Finding(
                category="ifsc_mismatch",
                pages=inv.pages,
                document_refs=[inv.doc_id],
                description=(
                    f"Invoice IFSC '{inv.vendor_ifsc}' for vendor '{inv.vendor_name}' "
                    f"doesn't match Vendor Master IFSC '{vendor.ifsc}'"
                ),
                reported_value=inv.vendor_ifsc,
                correct_value=vendor.ifsc,
            ))

    return findings


def detect_duplicate_expense(documents: List[Document], stores: dict) -> List[Finding]:
    """
    Same expense entry claimed in two different expense reports.

    Fix vs original:
    - Was `== 2` which silently dropped any group of 3+ (those fell through
      BOTH this detector and triple_expense_claim).
    - Now uses `== 2` correctly AND explicitly passes groups of exactly 2.
      Groups of 3+ are left for detect_triple_expense_claim.
      The key insight: `find_duplicates()` may return groups of any size;
      we must partition by size rather than filtering with `== 2`.
    """
    findings = []
    expense_index: ExpenseIndex = stores['expense_index']

    duplicates = expense_index.find_duplicates()

    for key, items in duplicates:
        # Triple (or more) claims are handled by a separate detector.
        # Only process groups of exactly 2 here.
        if len(items) != 2:
            continue

        report_ids = [doc_id for doc_id, _ in items]
        er_pages = []
        for doc_id, _ in items:
            er = expense_index.reports.get(doc_id)
            if er:
                er_pages.extend(er.pages)

        parts = key.split("|")
        date_part   = parts[0] if len(parts) > 0 else "unknown date"
        desc_part   = parts[1] if len(parts) > 1 else key
        amount_part = parts[2] if len(parts) > 2 else ""

        findings.append(Finding(
            category="duplicate_expense",
            pages=sorted(set(er_pages)),
            document_refs=report_ids,
            description=(
                f"Expense '{desc_part}' on {date_part} for ₹{amount_part} "
                f"claimed in reports: {', '.join(report_ids)}"
            ),
            reported_value=amount_part,
            correct_value=amount_part,
        ))

    return findings


def detect_date_cascade(documents: List[Document], stores: dict) -> List[Finding]:
    """
    Invoice date is before its own PO date (can't invoice before the PO exists).

    The core problem with previous versions: simply checking inv_date < po_date
    fires on every legitimate advance/framework invoice in the dataset, producing
    25+ false positives against the 5 planted needles.

    Correct approach — a date_cascade needle is structurally different from a
    normal early invoice:

    A LEGITIMATE early invoice arises when a vendor starts work before the PO
    is formally raised. In that case, at least some invoices against the same PO
    will have dates AFTER the PO date (i.e. the PO eventually catches up).

    A PLANTED date_cascade needle is one where THE PO DATE IS LATER THAN ALL
    invoices against it — the PO could not possibly have authorised any of
    the work, because every invoice predates it. That is the structural signal.

    Algorithm:
    1. Group all invoices by their PO reference.
    2. For each PO, check if ALL invoices against it predate the PO date.
       - If yes → every invoice in that group is a cascade needle.
       - If no  → the PO date is plausible (some invoices came after), skip.
    3. Apply a minimum gap floor of 7 days to suppress same-week edge cases
       where document dating is slightly off.
    """
    findings = []
    po_registry: PORegistry = stores['po_registry']
    invoice_registry: InvoiceRegistry = stores['invoice_registry']

    # Group invoices by PO ref
    invoices_by_po: Dict[str, list] = defaultdict(list)
    for inv_id, inv in invoice_registry.invoices.items():
        if inv.po_ref and inv.date:
            invoices_by_po[inv.po_ref].append(inv)

    for po_ref, inv_list in invoices_by_po.items():
        po = po_registry.get(po_ref)
        if not po or not po.date:
            continue

        po_date = parse_date_to_tuple(po.date)
        if not po_date:
            continue

        dated_invs = []
        for inv in inv_list:
            ords = []
            # Parse normal
            m = re.search(r'(\d{1,2})[/-](\d{1,2})[/-](\d{4})', inv.date or "")
            if m:
                p1, p2, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
                if p2 <= 12: ords.append(_date_to_ordinal((y, p2, p1))) # DD/MM
                if p1 <= 12 and p1 != p2: ords.append(_date_to_ordinal((y, p1, p2))) # MM/DD
            elif inv.date:
                # Text month
                d = parse_date_to_tuple(inv.date)
                if d: ords.append(_date_to_ordinal(d))
            if ords:
                dated_invs.append((ords, inv))

        if not dated_invs:
            continue
            
        # Parse PO date similarly
        po_ords = []
        m = re.search(r'(\d{1,2})[/-](\d{1,2})[/-](\d{4})', po.date)
        if m:
            p1, p2, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
            if p2 <= 12: po_ords.append(_date_to_ordinal((y, p2, p1)))
            if p1 <= 12 and p1 != p2: po_ords.append(_date_to_ordinal((y, p1, p2)))
        else:
            d = parse_date_to_tuple(po.date)
            if d: po_ords.append(_date_to_ordinal(d))
            
        if not po_ords:
            continue
            
        # To be a definitive cascade, the invoice MUST predate the PO under ALL plausible interpretations.
        # So we compare the LATEST plausible invoice date against the EARLIEST plausible PO date.
        min_po_ord = min(po_ords)
        
        all_predate = True
        for inv_ords, inv in dated_invs:
            max_inv_ord = max(inv_ords)
            gap_days = min_po_ord - max_inv_ord
            if gap_days < 7:
                all_predate = False
                break
                
        if not all_predate:
            continue

        # Consolidate into ONE finding per PO group
        all_pages = []
        inv_ids = []
        inv_dates = []
        
        for inv_ords, inv in dated_invs:
            # Vendor sanity check
            if inv.vendor_name and po.vendor_name:
                if fuzz.token_sort_ratio(
                    inv.vendor_name.lower(), po.vendor_name.lower()
                ) < 60:
                    continue
            
            all_pages.extend(inv.pages)
            inv_ids.append(inv.doc_id)
            inv_dates.append(inv.date)

        if inv_ids:
            findings.append(Finding(
                category="date_cascade",
                pages=sorted(list(set(all_pages + po.pages))),
                document_refs=inv_ids + [po_ref],
                description=(
                    f"Date cascade: All {len(inv_ids)} invoices for PO {po_ref} predate the PO date {po.date}. "
                    f"Invoices: {', '.join(inv_ids)}"
                ),
                reported_value=", ".join(inv_dates),
                correct_value=po.date,
            ))

    return findings


def _date_to_ordinal(ymd: Tuple[int, int, int]) -> int:
    """Convert (year, month, day) to a simple integer for gap arithmetic."""
    y, m, d = ymd
    return y * 365 + m * 30 + d


def detect_gstin_state_mismatch(documents: List[Document], stores: dict) -> List[Finding]:
    """
    First 2 digits of GSTIN (state code) don't match the vendor's address state.

    Fix vs original:
    - The two branches (vendor-master lookup vs invoice-address fallback) were
      not symmetric: if a vendor IS in the master but the master's own state is
      wrong, the `elif` branch that cross-checks the invoice address was never
      reached. Now both checks run independently and we deduplicate by doc_id.
    """
    findings = []
    seen_docs: set = set()

    for doc in documents:
        if doc.doc_type != DocType.INVOICE or not doc.parsed:
            continue

        inv: Invoice = doc.parsed
        if not inv.vendor_gstin or inv.doc_id in seen_docs:
            continue

        gstin_state = get_state_from_gstin(inv.vendor_gstin)
        if gstin_state == "Unknown":
            continue

        reported_value = f"{inv.vendor_gstin[:2]} ({gstin_state})"
        emitted = False

        # Branch 1: cross-check against Vendor Master state
        vendor = VENDOR_BY_GSTIN.get(inv.vendor_gstin)
        if vendor and normalize_state(vendor.state) != normalize_state(gstin_state):
            findings.append(Finding(
                category="gstin_state_mismatch",
                pages=inv.pages,
                document_refs=[inv.doc_id],
                description=(
                    f"GSTIN {inv.vendor_gstin} state code indicates '{gstin_state}' "
                    f"but invoice address shows '{inv.vendor_state or vendor.state}'"
                ),
                reported_value=reported_value,
                correct_value=inv.vendor_state or vendor.state,
            ))
            seen_docs.add(inv.doc_id)
            emitted = True

        # Branch 2: cross-check against address on the invoice itself
        if not emitted and inv.vendor_state:
            if normalize_state(inv.vendor_state) != normalize_state(gstin_state):
                findings.append(Finding(
                    category="gstin_state_mismatch",
                    pages=inv.pages,
                    document_refs=[inv.doc_id],
                    description=(
                        f"GSTIN {inv.vendor_gstin} state code indicates '{gstin_state}' "
                        f"but invoice address shows '{inv.vendor_state}'"
                    ),
                    reported_value=reported_value,
                    correct_value=inv.vendor_state,
                ))
                seen_docs.add(inv.doc_id)

    return findings


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

def run_medium_detectors(documents: List[Document], stores: dict) -> List[Finding]:
    """Run all medium-tier detectors."""
    print("\n🟡 Running MEDIUM detectors (Tier 2)...")

    all_findings = []

    detectors = [
        ("po_invoice_mismatch",  detect_po_invoice_mismatch),
        ("vendor_name_typo",     detect_vendor_name_typo),
        ("double_payment",       detect_double_payment),
        ("ifsc_mismatch",        detect_ifsc_mismatch),
        ("duplicate_expense",    detect_duplicate_expense),
        ("date_cascade",         detect_date_cascade),
        ("gstin_state_mismatch", detect_gstin_state_mismatch),
    ]

    for name, detector in detectors:
        found = detector(documents, stores)
        print(f"  {name}: {len(found)} findings")
        all_findings.extend(found)

    print(f"  Total medium findings: {len(all_findings)}")
    return all_findings
