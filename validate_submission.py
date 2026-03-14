"""
validate_submission.py  —  Two-level verification of output/submission.json

Level 1 (DB):  Re-query SQLite to confirm each finding's numbers are accurate.
Level 2 (PDF): Extract text from cited pages and check doc ref appears there.

Exit codes:
  0  all PASS
  1  some WARN or FAIL

Usage:
  python3 validate_submission.py                       # full validation
  python3 validate_submission.py --db-only             # skip PDF checks
  python3 validate_submission.py --category fake_vendor
  python3 validate_submission.py --finding F-076
"""

import argparse
import json
import os
import re
import sqlite3
import sys
from collections import defaultdict

import pypdf

import db_setup
import utils

SUBMISSION_PATH = os.path.join(os.path.dirname(__file__), 'output', 'submission_3.json')
PDF_PATH        = os.path.join(os.path.dirname(__file__), 'gauntlet.pdf')

# ── Result constants ───────────────────────────────────────────────────────────
PASS  = 'PASS'
WARN  = 'WARN'
FAIL  = 'FAIL'
SKIP  = 'SKIP'


def result(status, msg):
    return {'status': status, 'msg': msg}


# ── DB connection ──────────────────────────────────────────────────────────────

def get_conn(db_path=db_setup.DB_PATH):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


# ══════════════════════════════════════════════════════════════════════════════
# Level 1 — DB verifiers (one per category)
# ══════════════════════════════════════════════════════════════════════════════

def verify_arithmetic_error(f, conn):
    inv_no = f['document_refs'][0]
    cur = conn.cursor()
    cur.execute("SELECT subtotal, cgst, sgst, igst, grand_total FROM invoices WHERE invoice_no=?", (inv_no,))
    row = cur.fetchone()
    if not row:
        return result(WARN, f"Invoice {inv_no} not found in DB")
    reported = float(f['reported_value'])
    correct  = float(f['correct_value'])
    desc = f.get('description', '')

    if 'sum of line items' in desc:
        # subtotal ≠ sum(line items) check
        cur.execute("SELECT ROUND(SUM(amount),2) AS s FROM invoice_line_items WHERE invoice_no=?", (inv_no,))
        s = cur.fetchone()['s']
        if s is None:
            return result(WARN, "No line items found")
        if abs(reported - (row['subtotal'] or 0)) > 0.11:
            return result(WARN, f"reported_value {reported} ≠ DB subtotal {row['subtotal']}")
        if abs(correct - s) > 0.11:
            return result(WARN, f"correct_value {correct} ≠ computed sum {s}")
        return result(PASS, f"Subtotal {reported} ≠ line sum {s:.2f} confirmed")
    else:
        # grand_total ≠ subtotal + tax check
        st  = row['subtotal'] or 0
        tax = (row['cgst'] or 0) + (row['sgst'] or 0) + (row['igst'] or 0)
        expected_gt = round(st + tax, 2)
        actual_gt   = row['grand_total'] or 0
        if abs(reported - actual_gt) > 0.11:
            return result(WARN, f"reported_value {reported} ≠ DB grand_total {actual_gt}")
        if abs(actual_gt - expected_gt) > 0.10:
            return result(PASS, f"Grand total {actual_gt} ≠ subtotal+tax {expected_gt} confirmed")
        return result(WARN, f"Grand total {actual_gt} ≈ subtotal+tax {expected_gt} — borderline")


def verify_billing_typo(f, conn):
    inv_no = f['document_refs'][0]
    cur = conn.cursor()
    cur.execute("""
        SELECT qty, rate, amount FROM invoice_line_items
        WHERE invoice_no=? AND qty IS NOT NULL AND rate IS NOT NULL AND amount IS NOT NULL
    """, (inv_no,))
    typos = [(r['qty'], r['rate'], r['amount'])
             for r in cur.fetchall()
             if abs(round(r['qty'] * r['rate'], 2) - r['amount']) > 0.10]
    if not typos:
        return result(FAIL, f"No qty×rate≠amount lines found in {inv_no}")
    return result(PASS, f"{len(typos)} billing typo line(s) confirmed")


def verify_duplicate_line_item(f, conn):
    inv_no = f['document_refs'][0]
    cur = conn.cursor()
    cur.execute("""
        SELECT description, ROUND(qty,2) AS q, ROUND(rate,2) AS r, ROUND(amount,2) AS a, COUNT(*) AS cnt
        FROM invoice_line_items WHERE invoice_no=?
        GROUP BY description, ROUND(qty,2), ROUND(rate,2), ROUND(amount,2)
        HAVING cnt > 1
    """, (inv_no,))
    dups = cur.fetchall()
    if not dups:
        return result(FAIL, f"No duplicate line items found in {inv_no}")
    return result(PASS, f"{len(dups)} duplicate line(s) confirmed in {inv_no}")


def verify_invalid_date(f, conn):
    inv_no = f['document_refs'][0]
    cur = conn.cursor()
    # Check invoices table
    cur.execute("SELECT date FROM invoices WHERE invoice_no=?", (inv_no,))
    row = cur.fetchone()
    if row:
        d = row['date']
        if d:
            ok, reason = utils.validate_date(d)
            if not ok:
                return result(PASS, f"Invalid date '{d}': {reason}")
            if d > '2025-12-31':
                return result(PASS, f"Future date '{d}' confirmed")
        return result(WARN, f"Date '{d}' appears valid — may be in expense entry or PO")
    # Check expense reports
    cur.execute("SELECT period_start, period_end FROM expense_reports WHERE report_id=?", (inv_no,))
    row = cur.fetchone()
    if row:
        for d in [row['period_start'], row['period_end']]:
            if d:
                ok, reason = utils.validate_date(d)
                if not ok:
                    return result(PASS, f"Invalid date '{d}': {reason}")
    return result(WARN, f"Could not confirm invalid date for {inv_no}")


def verify_wrong_tax_rate(f, conn):
    inv_no = f['document_refs'][0]
    cur = conn.cursor()
    cur.execute("SELECT subtotal, cgst, sgst, igst FROM invoices WHERE invoice_no=?", (inv_no,))
    row = cur.fetchone()
    if not row:
        return result(WARN, f"Invoice {inv_no} not in DB")
    actual_tax = (row['cgst'] or 0) + (row['sgst'] or 0) + (row['igst'] or 0)
    cur.execute("SELECT hsn, amount FROM invoice_line_items WHERE invoice_no=? AND amount IS NOT NULL", (inv_no,))
    items = cur.fetchall()
    expected_tax = sum(i['amount'] * utils.expected_gst_rate(i['hsn']) / 100
                       for i in items if utils.expected_gst_rate(i['hsn']) is not None)
    diff = abs(actual_tax - expected_tax)
    if row['subtotal'] and diff / row['subtotal'] > 0.10:
        return result(PASS, f"Tax diff {diff:.2f} ({diff/row['subtotal']*100:.1f}% of subtotal) confirmed")
    return result(WARN, f"Tax diff {diff:.2f} is ≤10% of subtotal — borderline")


# ── Medium ─────────────────────────────────────────────────────────────────────

def verify_po_invoice_mismatch(f, conn):
    refs = f['document_refs']
    inv_no = refs[0]
    po_no  = refs[1] if len(refs) > 1 else None
    if not po_no:
        return result(WARN, "No PO ref in document_refs")
    reported = float(f['reported_value'])
    correct  = float(f['correct_value'])
    cur = conn.cursor()
    cur.execute("SELECT rate FROM invoice_line_items WHERE invoice_no=?", (inv_no,))
    inv_rates = {round(r['rate'], 2) for r in cur.fetchall() if r['rate']}
    cur.execute("SELECT rate FROM po_line_items WHERE po_no=?", (po_no,))
    po_rates  = {round(r['rate'], 2) for r in cur.fetchall() if r['rate']}
    if round(reported, 2) not in inv_rates:
        return result(FAIL, f"reported rate {reported} not found in {inv_no} line items {inv_rates}")
    if round(correct, 2) not in po_rates:
        return result(WARN, f"correct rate {correct} not found in {po_no} PO rates {po_rates}")
    return result(PASS, f"Invoice rate {reported} ≠ PO rate {correct} confirmed")


def verify_vendor_name_typo(f, conn):
    inv_no      = f['document_refs'][0]
    reported    = f['reported_value']
    correct     = f['correct_value']
    cur = conn.cursor()
    cur.execute("SELECT vendor_name FROM invoices WHERE invoice_no=?", (inv_no,))
    row = cur.fetchone()
    if not row:
        return result(WARN, f"Invoice {inv_no} not in DB")
    if row['vendor_name'] != reported:
        return result(FAIL, f"DB vendor_name '{row['vendor_name']}' ≠ reported '{reported}'")
    # Check master
    cur.execute("SELECT name FROM vendor_master WHERE name=?", (correct,))
    master = cur.fetchone()
    if not master:
        return result(WARN, f"Correct name '{correct}' not found in vendor_master")
    sim = utils.is_vendor_name_typo(reported, correct)
    if not sim:
        return result(WARN, f"Names differ but may not be a typo: '{reported}' vs '{correct}'")
    return result(PASS, f"Typo '{reported}' → '{correct}' confirmed")


def verify_double_payment(f, conn):
    refs   = f['document_refs']
    if len(refs) < 2:
        return result(WARN, "Need 2 statement refs")
    s1, s2 = refs[0], refs[1]
    amount_str = f['reported_value'].replace('(twice)', '').strip()
    try:
        amount = float(amount_str)
    except ValueError:
        return result(WARN, f"Could not parse amount from '{f['reported_value']}'")
    cur = conn.cursor()
    cur.execute("SELECT debit FROM bank_transactions WHERE stmt_id=? AND ABS(debit-?) < 1.0", (s1, amount))
    r1 = cur.fetchone()
    cur.execute("SELECT debit FROM bank_transactions WHERE stmt_id=? AND ABS(debit-?) < 1.0", (s2, amount))
    r2 = cur.fetchone()
    if not r1:
        return result(FAIL, f"Amount {amount} not found as debit in {s1}")
    if not r2:
        return result(FAIL, f"Amount {amount} not found as debit in {s2}")
    return result(PASS, f"Duplicate debit {amount} confirmed in {s1} and {s2}")


def verify_ifsc_mismatch(f, conn):
    inv_no  = f['document_refs'][0]
    reported = f['reported_value']
    correct  = f['correct_value']
    cur = conn.cursor()
    cur.execute("SELECT vendor_ifsc, vendor_gstin FROM invoices WHERE invoice_no=?", (inv_no,))
    row = cur.fetchone()
    if not row:
        return result(WARN, f"Invoice {inv_no} not in DB")
    if row['vendor_ifsc'] != reported:
        return result(FAIL, f"DB IFSC '{row['vendor_ifsc']}' ≠ reported '{reported}'")
    cur.execute("SELECT ifsc FROM vendor_master WHERE gstin=?", (row['vendor_gstin'],))
    master = cur.fetchone()
    if not master:
        return result(WARN, f"Vendor GSTIN {row['vendor_gstin']} not in vendor_master")
    if master['ifsc'] != correct:
        return result(WARN, f"Master IFSC '{master['ifsc']}' ≠ correct_value '{correct}'")
    return result(PASS, f"IFSC mismatch {reported} ≠ {correct} confirmed")


def verify_duplicate_expense(f, conn):
    refs = f['document_refs']
    if len(refs) < 2:
        return result(WARN, "Need 2 report refs")
    r1, r2 = refs[0], refs[1]
    amount_str = f['reported_value'].split('(')[0].strip()  # strip "(twice)" suffix
    try:
        amount = float(amount_str)
    except ValueError:
        return result(WARN, f"Cannot parse amount '{amount_str}'")
    cur = conn.cursor()
    cur.execute("SELECT amount, description FROM expense_entries WHERE report_id=? AND ABS(amount-?) < 1.0", (r1, amount))
    e1 = cur.fetchone()
    cur.execute("SELECT amount, description FROM expense_entries WHERE report_id=? AND ABS(amount-?) < 1.0", (r2, amount))
    e2 = cur.fetchone()
    if not e1:
        return result(FAIL, f"Amount {amount} not found in {r1}")
    if not e2:
        return result(FAIL, f"Amount {amount} not found in {r2}")
    return result(PASS, f"Duplicate expense {amount} confirmed in {r1} and {r2}")


def verify_date_cascade(f, conn):
    refs   = f['document_refs']
    inv_no = refs[0]
    po_no  = refs[1] if len(refs) > 1 else None
    if not po_no:
        return result(WARN, "No PO ref")
    cur = conn.cursor()
    cur.execute("SELECT date FROM invoices WHERE invoice_no=?", (inv_no,))
    inv = cur.fetchone()
    cur.execute("SELECT date FROM purchase_orders WHERE po_no=?", (po_no,))
    po  = cur.fetchone()
    if not inv or not po:
        return result(WARN, "Invoice or PO not found")
    inv_date = inv['date']
    po_date  = po['date']
    if not inv_date or not po_date:
        return result(WARN, "Date fields are NULL")
    if inv_date < po_date:
        return result(PASS, f"Invoice {inv_date} < PO {po_date} confirmed")
    return result(FAIL, f"Invoice {inv_date} is NOT before PO {po_date}")


def verify_gstin_state_mismatch(f, conn):
    inv_no = f['document_refs'][0]
    cur = conn.cursor()
    cur.execute("SELECT vendor_name, vendor_gstin FROM invoices WHERE invoice_no=?", (inv_no,))
    row = cur.fetchone()
    if not row or not row['vendor_gstin']:
        return result(WARN, f"Invoice {inv_no} missing vendor_gstin")
    inv_state = row['vendor_gstin'][:2]
    cur.execute("SELECT gstin FROM vendor_master WHERE LOWER(name)=LOWER(?)", (row['vendor_name'],))
    master = cur.fetchone()
    if not master or not master['gstin']:
        return result(WARN, f"Vendor '{row['vendor_name']}' not in vendor_master")
    master_state = master['gstin'][:2]
    if inv_state != master_state:
        return result(PASS, f"State code mismatch: invoice={inv_state} master={master_state} confirmed")
    return result(FAIL, f"State codes match ({inv_state}) — not a mismatch")


# ── Evil ───────────────────────────────────────────────────────────────────────

def verify_quantity_accumulation(f, conn):
    refs    = f['document_refs']
    po_no   = refs[-1]
    inv_nos = refs[:-1]
    reported = float(f['reported_value'])
    cur = conn.cursor()
    cur.execute("""
        SELECT ROUND(SUM(ili.qty),2) AS total_qty
        FROM invoices i
        JOIN invoice_line_items ili ON i.invoice_no = ili.invoice_no
        WHERE i.po_ref=? AND ili.qty IS NOT NULL
        AND i.invoice_no IN ({})
    """.format(','.join('?' * len(inv_nos))), [po_no] + inv_nos)
    row = cur.fetchone()
    if not row or row['total_qty'] is None:
        return result(WARN, f"No qtys found for PO {po_no}")
    # reported_value is qty for ONE description, total might be different
    # Just check that accumulated qty exceeds PO qty
    cur.execute("SELECT SUM(qty) AS pq FROM po_line_items WHERE po_no=?", (po_no,))
    prow = cur.fetchone()
    po_qty = prow['pq'] if prow and prow['pq'] else 0
    if row['total_qty'] > po_qty * 1.20:
        return result(PASS, f"Accumulated qty {row['total_qty']:.0f} > PO qty {po_qty:.0f} confirmed")
    return result(WARN, f"Total qty {row['total_qty']:.0f} vs PO qty {po_qty:.0f} — may be partial match")


def verify_price_escalation(f, conn):
    refs    = f['document_refs']
    po_no   = refs[-1]
    inv_nos = refs[:-1]
    reported = float(f['reported_value'])  # min invoice rate
    correct  = float(f['correct_value'])   # PO rate
    cur = conn.cursor()
    cur.execute("SELECT MIN(rate) AS min_rate FROM invoice_line_items WHERE invoice_no IN ({})".format(
        ','.join('?' * len(inv_nos))), inv_nos)
    row = cur.fetchone()
    if not row or row['min_rate'] is None:
        return result(WARN, "No rates found in invoice line items")
    if row['min_rate'] > correct * 1.01:
        return result(PASS, f"Min invoice rate {row['min_rate']:.2f} > PO rate {correct:.2f} confirmed")
    return result(WARN, f"Min invoice rate {row['min_rate']:.2f} vs PO rate {correct:.2f} — borderline")


def verify_balance_drift(f, conn):
    refs = f['document_refs']
    if len(refs) < 2:
        return result(WARN, "Need 2 statement refs")
    curr_id, prev_id = refs[0], refs[1]
    reported = float(f['reported_value'])
    correct  = float(f['correct_value'])
    cur = conn.cursor()
    cur.execute("SELECT opening_balance FROM bank_statements WHERE stmt_id=?", (curr_id,))
    curr = cur.fetchone()
    cur.execute("SELECT closing_balance FROM bank_statements WHERE stmt_id=?", (prev_id,))
    prev = cur.fetchone()
    if not curr or not prev:
        return result(WARN, f"Statement(s) not found: {curr_id}, {prev_id}")
    ob = curr['opening_balance']
    cb = prev['closing_balance']
    if ob is None or cb is None:
        return result(WARN, "Balance fields are NULL")
    if abs(ob - reported) > 0.02:
        return result(FAIL, f"Opening balance {ob:.2f} ≠ reported {reported:.2f}")
    if abs(cb - correct) > 0.02:
        return result(FAIL, f"Closing balance {cb:.2f} ≠ correct_value {correct:.2f}")
    if abs(ob - cb) > 1.0:
        return result(PASS, f"Drift confirmed: {curr_id} opens at {ob:.2f}, {prev_id} closes at {cb:.2f}")
    return result(FAIL, f"Balances match ({ob:.2f} ≈ {cb:.2f}) — not a real drift")


def verify_circular_reference(f, conn):
    refs = f['document_refs']
    cur  = conn.cursor()
    # Build edge map: doc → original_ref
    edges = {}
    cur.execute("SELECT cn_no, original_ref FROM credit_notes WHERE original_ref IS NOT NULL")
    for r in cur.fetchall():
        edges[r['cn_no']] = r['original_ref']
    cur.execute("SELECT dn_no, original_ref FROM debit_notes WHERE original_ref IS NOT NULL")
    for r in cur.fetchall():
        edges[r['dn_no']] = r['original_ref']

    # Walk from each ref and check if we return to a starting node
    def has_cycle_through(start):
        visited, node = [], start
        for _ in range(20):
            if node in visited:
                return True   # found a cycle
            visited.append(node)
            next_node = edges.get(node)
            if not next_node or next_node not in edges:
                return False  # hit a dead end or terminal
            node = next_node
        return False

    # Verify all cited refs exist in the edge map
    found = [r for r in refs if r in edges]
    if len(found) < 2:
        # Check if they exist as CN/DN at all even without outgoing edges
        for ref in refs:
            cur.execute("SELECT cn_no FROM credit_notes WHERE cn_no=?", (ref,))
            if cur.fetchone() and ref not in found:
                found.append(ref)
            cur.execute("SELECT dn_no FROM debit_notes WHERE dn_no=?", (ref,))
            if cur.fetchone() and ref not in found:
                found.append(ref)
        return result(WARN, f"Only {len(found)}/{len(refs)} cycle refs traceable: {found}")

    # Check that starting from any ref we eventually cycle back
    cycling = [r for r in found if has_cycle_through(r)]
    if cycling:
        return result(PASS, f"Cycle confirmed from {cycling}")
    return result(WARN, f"Refs exist ({found}) but cycle not confirmed — chain may end outside dataset")


def verify_triple_expense_claim(f, conn):
    refs = f['document_refs']
    if len(refs) < 3:
        return result(FAIL, "Need 3 report refs for triple claim")
    cur = conn.cursor()
    amounts = []
    for r in refs[:3]:
        cur.execute("SELECT amount FROM expense_entries WHERE report_id=? ORDER BY amount DESC LIMIT 10", (r,))
        amounts.append({round(row['amount'], 0) for row in cur.fetchall()})
    common = amounts[0] & amounts[1] & amounts[2]
    if common:
        return result(PASS, f"Common amount(s) {common} found in all 3 reports")
    return result(WARN, f"No common amounts across {refs[:3]}")


def verify_employee_id_collision(f, conn):
    cur = conn.cursor()
    emp_id = f['reported_value']
    cur.execute("""
        SELECT GROUP_CONCAT(DISTINCT employee_name) AS names, COUNT(DISTINCT employee_name) AS cnt
        FROM expense_reports WHERE employee_id=?
    """, (emp_id,))
    row = cur.fetchone()
    if not row or row['cnt'] is None:
        return result(WARN, f"Employee ID {emp_id} not found in DB")
    if row['cnt'] < 2:
        return result(FAIL, f"Employee ID {emp_id} only linked to {row['cnt']} name(s): {row['names']}")
    return result(PASS, f"ID {emp_id} used by {row['cnt']} names: {row['names']}")


def verify_fake_vendor(f, conn):
    inv_no  = f['document_refs'][0]
    reported = f['reported_value']  # GSTIN
    cur = conn.cursor()
    cur.execute("SELECT vendor_gstin FROM invoices WHERE invoice_no=?", (inv_no,))
    row = cur.fetchone()
    if not row:
        return result(WARN, f"Invoice {inv_no} not in DB")
    if row['vendor_gstin'] != reported:
        return result(FAIL, f"DB GSTIN '{row['vendor_gstin']}' ≠ reported '{reported}'")
    cur.execute("SELECT gstin FROM vendor_master WHERE gstin=?", (reported,))
    master = cur.fetchone()
    if master:
        return result(FAIL, f"GSTIN {reported} IS in vendor_master — not a fake vendor")
    return result(PASS, f"GSTIN {reported} confirmed absent from vendor_master")


def verify_phantom_po_reference(f, conn):
    refs   = f['document_refs']
    inv_no = refs[0]
    po_no  = refs[1] if len(refs) > 1 else f['reported_value']
    cur = conn.cursor()
    cur.execute("SELECT po_ref FROM invoices WHERE invoice_no=?", (inv_no,))
    row = cur.fetchone()
    if not row:
        return result(WARN, f"Invoice {inv_no} not in DB")
    if row['po_ref'] != po_no:
        return result(WARN, f"DB po_ref '{row['po_ref']}' ≠ reported '{po_no}'")
    cur.execute("SELECT po_no FROM purchase_orders WHERE po_no=?", (po_no,))
    if cur.fetchone():
        return result(FAIL, f"PO {po_no} DOES exist in purchase_orders — not phantom")
    return result(PASS, f"PO {po_no} confirmed absent from purchase_orders")


# ── Dispatcher ─────────────────────────────────────────────────────────────────

DB_VERIFIERS = {
    'arithmetic_error':     verify_arithmetic_error,
    'billing_typo':         verify_billing_typo,
    'duplicate_line_item':  verify_duplicate_line_item,
    'invalid_date':         verify_invalid_date,
    'wrong_tax_rate':       verify_wrong_tax_rate,
    'po_invoice_mismatch':  verify_po_invoice_mismatch,
    'vendor_name_typo':     verify_vendor_name_typo,
    'double_payment':       verify_double_payment,
    'ifsc_mismatch':        verify_ifsc_mismatch,
    'duplicate_expense':    verify_duplicate_expense,
    'date_cascade':         verify_date_cascade,
    'gstin_state_mismatch': verify_gstin_state_mismatch,
    'quantity_accumulation':verify_quantity_accumulation,
    'price_escalation':     verify_price_escalation,
    'balance_drift':        verify_balance_drift,
    'circular_reference':   verify_circular_reference,
    'triple_expense_claim': verify_triple_expense_claim,
    'employee_id_collision':verify_employee_id_collision,
    'fake_vendor':          verify_fake_vendor,
    'phantom_po_reference': verify_phantom_po_reference,
}


# ══════════════════════════════════════════════════════════════════════════════
# Level 2 — PDF page verifier
# ══════════════════════════════════════════════════════════════════════════════

_pdf_cache: dict[int, str] = {}

def get_page_text(reader: pypdf.PdfReader, page_no: int) -> str:
    """Return cached text for 1-indexed page_no."""
    if page_no not in _pdf_cache:
        if page_no < 1 or page_no > len(reader.pages):
            return ''
        _pdf_cache[page_no] = reader.pages[page_no - 1].extract_text() or ''
    return _pdf_cache[page_no]


def verify_pdf_pages(f, reader: pypdf.PdfReader):
    """
    Check that the primary document ref (first of document_refs) appears
    somewhere in the cited pages text.
    """
    pages = f.get('pages', [])
    refs  = f.get('document_refs', [])
    if not pages or not refs:
        return result(SKIP, "No pages or refs to check")

    primary_ref = refs[0]
    # Some refs are PO numbers or stmt IDs — check any ref that looks like a doc ID
    candidates = [r for r in refs if re.match(r'^[A-Z]{2,}[-–]', r)]
    if not candidates:
        return result(SKIP, "No document-style refs to search for")

    combined_text = ' '.join(get_page_text(reader, p) for p in pages[:5])  # cap at 5 pages
    if not combined_text.strip():
        return result(WARN, f"Pages {pages} returned empty text from PDF")

    found = [r for r in candidates if r in combined_text]
    if found:
        return result(PASS, f"Ref(s) {found} found in page text")

    # Fallback: check just the numeric part (e.g. "00042" from "INV-2025-00042")
    numeric_hits = []
    for r in candidates:
        nums = re.findall(r'\d{5,}', r)
        if any(n in combined_text for n in nums):
            numeric_hits.append(r)
    if numeric_hits:
        return result(WARN, f"Ref numeric part found but full ref not literal: {numeric_hits}")

    return result(WARN, f"Primary ref '{primary_ref}' not found on page(s) {pages} — page may be off by 1")


# ══════════════════════════════════════════════════════════════════════════════
# Main runner
# ══════════════════════════════════════════════════════════════════════════════

STATUS_ICON = {PASS: '✓', WARN: '⚠', FAIL: '✗', SKIP: '·'}
STATUS_ORDER = {PASS: 0, WARN: 1, FAIL: 2, SKIP: 3}


def run_validation(
    submission_path=SUBMISSION_PATH,
    db_path=db_setup.DB_PATH,
    pdf_path=PDF_PATH,
    db_only=False,
    filter_category=None,
    filter_finding=None,
    verbose=False,
):
    with open(submission_path) as fh:
        submission = json.load(fh)

    findings = submission['findings']
    if filter_category:
        findings = [f for f in findings if f['category'] == filter_category]
    if filter_finding:
        findings = [f for f in findings if f['finding_id'] == filter_finding]

    conn   = get_conn(db_path)
    reader = None if db_only else pypdf.PdfReader(pdf_path)

    # Results: list of (finding_id, category, db_result, pdf_result)
    rows = []
    for f in findings:
        fid  = f['finding_id']
        cat  = f['category']
        verifier = DB_VERIFIERS.get(cat)

        if verifier:
            try:
                db_res = verifier(f, conn)
            except Exception as e:
                db_res = result(WARN, f"Exception: {e}")
        else:
            db_res = result(SKIP, "No DB verifier for this category")

        if reader:
            try:
                pdf_res = verify_pdf_pages(f, reader)
            except Exception as e:
                pdf_res = result(WARN, f"PDF exception: {e}")
        else:
            pdf_res = result(SKIP, "PDF check skipped")

        rows.append((fid, cat, db_res, pdf_res))

    conn.close()

    # ── Print report ────────────────────────────────────────────────────────
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

    print(f"\n{'='*75}")
    print(f"  SUBMISSION VALIDATION REPORT — {submission['team_id']}")
    print(f"  Source: {submission_path}")
    print(f"{'='*75}")
    print(f"  {'ID':<8} {'Category':<25} {'DB':<5} {'PDF':<5}  DB Message")
    print(f"  {'-'*8} {'-'*25} {'-'*5} {'-'*5}  {'-'*30}")

    counts = defaultdict(int)
    cat_issues = defaultdict(list)

    for fid, cat, db_res, pdf_res in rows:
        db_s  = db_res['status']
        pdf_s = pdf_res['status']
        icon_db  = STATUS_ICON[db_s]
        icon_pdf = STATUS_ICON[pdf_s]
        counts[db_s] += 1

        # Always print FAIL; print WARN/PASS only if verbose
        if db_s == FAIL or verbose:
            print(f"  {fid:<8} {cat:<25} {icon_db:<5} {icon_pdf:<5}  {db_res['msg'][:55]}")
            if pdf_s in (WARN, FAIL) and not db_only:
                print(f"  {'':8} {'':25} {'':5} {'':5}  PDF: {pdf_res['msg'][:55]}")
        elif db_s == WARN:
            print(f"  {fid:<8} {cat:<25} {icon_db:<5} {icon_pdf:<5}  {db_res['msg'][:55]}")

        if db_s in (WARN, FAIL):
            cat_issues[cat].append((fid, db_s, db_res['msg']))

    # ── Summary ─────────────────────────────────────────────────────────────
    total = len(rows)
    n_pass = counts[PASS]
    n_warn = counts[WARN]
    n_fail = counts[FAIL]
    n_skip = counts[SKIP]

    print(f"\n{'='*75}")
    print(f"  SUMMARY")
    print(f"{'='*75}")
    print(f"  Total findings checked : {total}")
    print(f"  {STATUS_ICON[PASS]} PASS  : {n_pass:>4}  ({n_pass/total*100:.0f}%)")
    print(f"  {STATUS_ICON[WARN]} WARN  : {n_warn:>4}  ({n_warn/total*100:.0f}%)  — verify manually")
    print(f"  {STATUS_ICON[FAIL]} FAIL  : {n_fail:>4}  ({n_fail/total*100:.0f}%)  — likely false positives")
    print(f"  {STATUS_ICON[SKIP]} SKIP  : {n_skip:>4}  ({n_skip/total*100:.0f}%)")

    # Score estimate
    confirmed_score = sum(WEIGHTS.get(cat, 0)
                          for _, cat, db_res, _ in rows if db_res['status'] == PASS)
    at_risk_score   = sum(WEIGHTS.get(cat, 0)
                          for _, cat, db_res, _ in rows if db_res['status'] in (WARN, SKIP))
    fp_penalty      = n_fail * 0.5

    print(f"\n  Score estimate (if all PASS findings are correct):")
    print(f"    Confirmed score  : {confirmed_score} pts  ({n_pass} findings)")
    print(f"    At-risk score    : {at_risk_score} pts  ({n_warn + n_skip} findings — WARN/SKIP)")
    print(f"    FP penalty risk  : -{fp_penalty:.1f} pts  ({n_fail} FAIL findings × 0.5)")
    print(f"    Net floor        : {confirmed_score - fp_penalty:.1f} pts")

    if cat_issues:
        print(f"\n  Categories needing review:")
        for cat in sorted(cat_issues, key=lambda c: -WEIGHTS.get(c, 0)):
            issues = cat_issues[cat]
            n_f = sum(1 for _, s, _ in issues if s == FAIL)
            n_w = sum(1 for _, s, _ in issues if s == WARN)
            pts = WEIGHTS.get(cat, 0)
            print(f"    [{pts}pt] {cat:<28} {n_f} FAIL, {n_w} WARN")

    print(f"{'='*75}\n")

    return n_fail


# ══════════════════════════════════════════════════════════════════════════════

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Validate submission.json')
    parser.add_argument('--submission', default=SUBMISSION_PATH, help='Path to submission.json')
    parser.add_argument('--db',         default=db_setup.DB_PATH,   help='SQLite DB path')
    parser.add_argument('--pdf',        default=PDF_PATH,            help='PDF path')
    parser.add_argument('--db-only',    action='store_true',         help='Skip PDF verification')
    parser.add_argument('--category',   default=None,                help='Filter to one category')
    parser.add_argument('--finding',    default=None,                help='Filter to one finding ID')
    parser.add_argument('--verbose',    action='store_true',         help='Print all rows including PASS')
    args = parser.parse_args()

    n_fail = run_validation(
        submission_path=args.submission,
        db_path=args.db,
        pdf_path=args.pdf,
        db_only=args.db_only,
        filter_category=args.category,
        filter_finding=args.finding,
        verbose=args.verbose,
    )
    sys.exit(0 if n_fail == 0 else 1)
