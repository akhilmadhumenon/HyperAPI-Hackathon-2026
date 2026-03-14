"""
Validation engine: deterministic SQL + Python queries to detect all 20 needle categories.
Returns a list of raw finding dicts for findings_builder to format.
"""
import sqlite3
import re
from collections import defaultdict

import db_setup
import utils


def get_conn(db_path: str = db_setup.DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


# ─── EASY NEEDLES ──────────────────────────────────────────────────────────────

def check_arithmetic_error(conn) -> list[dict]:
    """
    1. subtotal != sum(line_items.amount)
    2. grand_total != subtotal + cgst + sgst   (or + igst for IGST invoices)
    """
    findings = []
    cur = conn.cursor()

    # Check 1: subtotal vs line item sum
    cur.execute("""
        SELECT i.invoice_no, i.page_start, i.page_end,
               i.subtotal,
               ROUND(SUM(li.amount), 2) AS computed_subtotal
        FROM invoices i
        JOIN invoice_line_items li ON i.invoice_no = li.invoice_no
        WHERE i.subtotal IS NOT NULL
        GROUP BY i.invoice_no
        HAVING ABS(i.subtotal - computed_subtotal) > 0.10
    """)
    for row in cur.fetchall():
        findings.append({
            'category': 'arithmetic_error',
            'pages': list(range(row['page_start'], row['page_end'] + 1)),
            'document_refs': [row['invoice_no']],
            'description': (f"Subtotal {row['subtotal']:.2f} ≠ sum of line items "
                            f"{row['computed_subtotal']:.2f}"),
            'reported_value': f"{row['subtotal']:.2f}",
            'correct_value': f"{row['computed_subtotal']:.2f}",
        })

    # Check 2: grand_total vs subtotal + taxes
    cur.execute("""
        SELECT invoice_no, page_start, page_end,
               subtotal, cgst, sgst, igst, grand_total
        FROM invoices
        WHERE grand_total IS NOT NULL
          AND subtotal    IS NOT NULL
    """)
    for row in cur.fetchall():
        cgst = row['cgst'] or 0.0
        sgst = row['sgst'] or 0.0
        igst = row['igst'] or 0.0
        tax  = cgst + sgst + igst
        expected = round(row['subtotal'] + tax, 2)
        actual   = round(row['grand_total'], 2)
        if abs(expected - actual) > 0.10:
            # Check if this invoice is already in findings (avoid duplicates)
            if not any(f['document_refs'] == [row['invoice_no']] for f in findings):
                findings.append({
                    'category': 'arithmetic_error',
                    'pages': list(range(row['page_start'], row['page_end'] + 1)),
                    'document_refs': [row['invoice_no']],
                    'description': (f"Grand total {actual:.2f} ≠ subtotal {row['subtotal']:.2f} "
                                    f"+ tax {tax:.2f} = {expected:.2f}"),
                    'reported_value': f"{actual:.2f}",
                    'correct_value': f"{expected:.2f}",
                })
    return findings


def check_billing_typo(conn) -> list[dict]:
    """
    qty × rate ≠ amount in line items.
    Typical typo: 0.15 hours logged instead of 0.25 (decimal vs HH:MM confusion).
    """
    findings = []
    cur = conn.cursor()
    cur.execute("""
        SELECT li.invoice_no, li.line_num, li.description,
               li.qty, li.rate, li.amount,
               i.page_start, i.page_end
        FROM invoice_line_items li
        JOIN invoices i ON li.invoice_no = i.invoice_no
        WHERE li.qty IS NOT NULL AND li.rate IS NOT NULL AND li.amount IS NOT NULL
    """)
    for row in cur.fetchall():
        expected = round(row['qty'] * row['rate'], 2)
        actual   = round(row['amount'], 2)
        if abs(expected - actual) > 0.10:
            findings.append({
                'category': 'billing_typo',
                'pages': list(range(row['page_start'], row['page_end'] + 1)),
                'document_refs': [row['invoice_no']],
                'description': (f"Line {row['line_num']} ({row['description'][:50]}): "
                                f"qty {row['qty']} × rate {row['rate']:.2f} = {expected:.2f} "
                                f"but amount shows {actual:.2f}"),
                'reported_value': f"{actual:.2f}",
                'correct_value': f"{expected:.2f}",
            })
    return findings


def check_duplicate_line_item(conn) -> list[dict]:
    """Same description + amount appearing twice in the same invoice."""
    findings = []
    cur = conn.cursor()
    cur.execute("""
        SELECT li.invoice_no,
               li.description, li.hsn, li.qty, li.rate, li.amount,
               COUNT(*) AS cnt,
               i.page_start, i.page_end
        FROM invoice_line_items li
        JOIN invoices i ON li.invoice_no = i.invoice_no
        WHERE li.amount IS NOT NULL
        GROUP BY li.invoice_no, li.description, li.hsn, ROUND(li.qty,4), ROUND(li.rate,4)
        HAVING cnt > 1
    """)
    for row in cur.fetchall():
        findings.append({
            'category': 'duplicate_line_item',
            'pages': list(range(row['page_start'], row['page_end'] + 1)),
            'document_refs': [row['invoice_no']],
            'description': (f"Line item '{row['description'][:60]}' "
                            f"(qty={row['qty']}, rate={row['rate']:.2f}) "
                            f"appears {row['cnt']} times"),
            'reported_value': f"{row['amount']:.2f} × {row['cnt']}",
            'correct_value': f"{row['amount']:.2f} × 1",
        })
    return findings


def check_invalid_date(conn) -> list[dict]:
    """Impossible calendar dates: Feb 31, Sep 31, day 00, day 32, Feb 29 non-leap."""
    findings = []
    cur = conn.cursor()

    # Collect all dated documents
    sources = []
    for tbl, ref_col, date_col, pg_start, pg_end in [
        ('invoices',        'invoice_no', 'date',         'page_start', 'page_end'),
        ('purchase_orders', 'po_no',      'date',         'page_start', 'page_end'),
        ('expense_reports', 'report_id',  'date',         'page_start', 'page_end'),
        ('bank_statements', 'stmt_id',    'period_start', 'page_start', 'page_end'),
    ]:
        cur.execute(f"SELECT {ref_col}, {date_col}, {pg_start}, {pg_end} FROM {tbl} WHERE {date_col} IS NOT NULL")
        for row in cur.fetchall():
            sources.append((row[0], row[1], row[2], row[3]))

    # Also check expense entry dates
    cur.execute("""
        SELECT ee.report_id, ee.entry_date, er.page_start, er.page_end
        FROM expense_entries ee
        JOIN expense_reports er ON ee.report_id = er.report_id
        WHERE ee.entry_date IS NOT NULL
    """)
    for row in cur.fetchall():
        sources.append((row['report_id'], row['entry_date'], row['page_start'], row['page_end']))

    seen = set()
    for doc_ref, date_str, pg_start, pg_end in sources:
        if not date_str:
            continue
        is_valid, reason = utils.validate_date(date_str)
        if not is_valid:
            key = (doc_ref, date_str)
            if key not in seen:
                seen.add(key)
                findings.append({
                    'category': 'invalid_date',
                    'pages': [pg_start],
                    'document_refs': [doc_ref],
                    'description': f"Invalid date {date_str}: {reason}",
                    'reported_value': date_str,
                    'correct_value': 'N/A',
                })

    return findings


def check_wrong_tax_rate(conn) -> list[dict]:
    """
    GST rate on invoice doesn't match expected rates for its line items.
    Strategy: compute expected_tax = sum(item_amount × expected_rate(hsn) / 100)
    Then compare to actual (cgst + sgst + igst).
    Only flag when actual differs from expected by >5% of subtotal (very conservative).
    """
    findings = []
    cur = conn.cursor()

    cur.execute("""
        SELECT i.invoice_no, i.page_start, i.page_end,
               i.subtotal, i.cgst, i.sgst, i.igst
        FROM invoices i
        WHERE i.subtotal > 0
          AND (i.cgst IS NOT NULL OR i.sgst IS NOT NULL OR i.igst IS NOT NULL)
    """)
    invoices = [dict(r) for r in cur.fetchall()]

    cur2 = conn.cursor()
    for inv in invoices:
        cgst = inv['cgst'] or 0.0
        sgst = inv['sgst'] or 0.0
        igst = inv['igst'] or 0.0
        actual_tax = cgst + sgst + igst

        cur2.execute("""
            SELECT hsn, amount FROM invoice_line_items
            WHERE invoice_no = ? AND amount IS NOT NULL
        """, (inv['invoice_no'],))
        items = cur2.fetchall()
        if not items:
            continue

        # Compute expected tax per item based on HSN
        expected_tax = 0.0
        unknown_amount = 0.0
        for item in items:
            rate = utils.expected_gst_rate(item['hsn'])
            if rate is not None:
                expected_tax += item['amount'] * rate / 100.0
            else:
                unknown_amount += item['amount']

        # If >30% of invoice amount has unknown HSN rates, skip (too uncertain)
        if unknown_amount > inv['subtotal'] * 0.30:
            continue
        if expected_tax == 0:
            continue

        diff = abs(actual_tax - expected_tax)
        # Only flag when difference is > 10% of subtotal (catches rate substitutions like 5%→18%)
        if diff / inv['subtotal'] > 0.10:
            effective = round(actual_tax / inv['subtotal'] * 100, 1)
            exp_pct   = round(expected_tax / inv['subtotal'] * 100, 1)
            findings.append({
                'category': 'wrong_tax_rate',
                'pages': list(range(inv['page_start'], inv['page_end'] + 1)),
                'document_refs': [inv['invoice_no']],
                'description': (f"Tax charged {actual_tax:.2f} ({effective}%) ≠ "
                                f"expected {expected_tax:.2f} ({exp_pct}%) "
                                f"based on HSN codes"),
                'reported_value': f"{effective}%",
                'correct_value': f"{exp_pct}%",
            })
    return findings


# ─── MEDIUM NEEDLES ────────────────────────────────────────────────────────────

def check_po_invoice_mismatch(conn) -> list[dict]:
    """
    Invoice total or line item qty/rate differs significantly from the linked PO.
    Only fires when an invoice has a po_ref and the PO exists in our DB.
    """
    findings = []
    cur = conn.cursor()

    # Per-item rate mismatch: for each invoice line item, find the BEST matching PO line item
    # Use GROUP BY to avoid cross-product explosions when descriptions repeat
    cur.execute("""
        SELECT i.invoice_no, i.po_ref,
               ili.description,
               ili.rate        AS inv_rate,
               MIN(pli.rate)   AS po_rate_min,
               MAX(pli.rate)   AS po_rate_max,
               ili.qty         AS inv_qty,
               MIN(pli.qty)    AS po_qty_min,
               i.page_start,   i.page_end
        FROM invoices i
        JOIN invoice_line_items ili ON i.invoice_no = ili.invoice_no
        JOIN purchase_orders po     ON i.po_ref = po.po_no
        JOIN po_line_items pli      ON pli.po_no = po.po_no
                                   AND pli.description = ili.description
        WHERE ili.rate IS NOT NULL AND pli.rate IS NOT NULL
          AND ili.qty  IS NOT NULL AND pli.qty  IS NOT NULL
        GROUP BY i.invoice_no, ili.description, ili.rate, ili.qty
        HAVING ABS(ili.rate - po_rate_min) / MAX(po_rate_min, 0.01) > 0.05
           AND ABS(ili.rate - po_rate_max) / MAX(po_rate_max, 0.01) > 0.05
    """)
    seen = set()
    for row in cur.fetchall():
        key = (row['invoice_no'], row['description'][:30])
        if key in seen:
            continue
        seen.add(key)
        po_rate = row['po_rate_min']  # compare to most favorable PO rate
        findings.append({
            'category': 'po_invoice_mismatch',
            'pages': list(range(row['page_start'], row['page_end'] + 1)),
            'document_refs': [row['invoice_no'], row['po_ref']],
            'description': (f"Line '{row['description'][:50]}': "
                            f"invoice rate {row['inv_rate']:.2f} vs PO rate {po_rate:.2f}"),
            'reported_value': f"{row['inv_rate']:.2f}",
            'correct_value': f"{po_rate:.2f}",
        })
    return findings


def check_vendor_name_typo(conn) -> list[dict]:
    """Invoice vendor name is misspelled vs the Vendor Master."""
    findings = []
    cur = conn.cursor()

    cur.execute("SELECT * FROM vendor_master")
    vendors = [dict(r) for r in cur.fetchall()]
    if not vendors:
        return findings

    cur.execute("SELECT invoice_no, vendor_name, vendor_gstin, page_start, page_end FROM invoices WHERE vendor_name IS NOT NULL")
    for row in cur.fetchall():
        inv_name = row['vendor_name']
        inv_gstin = row['vendor_gstin']

        # First try to match by GSTIN (authoritative)
        matched_by_gstin = None
        if inv_gstin:
            for v in vendors:
                if v['gstin'] == inv_gstin:
                    matched_by_gstin = v
                    break

        if matched_by_gstin:
            master_name = matched_by_gstin['name']
            if utils.is_vendor_name_typo(inv_name, master_name):
                findings.append({
                    'category': 'vendor_name_typo',
                    'pages': [row['page_start']],
                    'document_refs': [row['invoice_no']],
                    'description': (f"Vendor name '{inv_name}' misspelled vs "
                                    f"master '{master_name}'"),
                    'reported_value': inv_name,
                    'correct_value': master_name,
                })
        else:
            # Try name match
            best = utils.find_vendor_match(inv_name, vendors, threshold=0.75)
            if best and utils.is_vendor_name_typo(inv_name, best['name']):
                findings.append({
                    'category': 'vendor_name_typo',
                    'pages': [row['page_start']],
                    'document_refs': [row['invoice_no']],
                    'description': (f"Vendor name '{inv_name}' misspelled vs "
                                    f"master '{best['name']}'"),
                    'reported_value': inv_name,
                    'correct_value': best['name'],
                })
    return findings


def check_double_payment(conn) -> list[dict]:
    """Same payment (vendor, amount, ref) appears in two different bank statements."""
    findings = []
    cur = conn.cursor()

    cur.execute("""
        SELECT bt1.stmt_id AS stmt1, bt2.stmt_id AS stmt2,
               bt1.description, bt1.debit AS amount,
               bt1.ref AS ref1, bt2.ref AS ref2,
               bs1.page_start AS pg1, bs2.page_start AS pg2
        FROM bank_transactions bt1
        JOIN bank_transactions bt2
          ON bt1.stmt_id != bt2.stmt_id
         AND ABS(COALESCE(bt1.debit,0) - COALESCE(bt2.debit,0)) < 1.0
         AND bt1.debit IS NOT NULL AND bt1.debit > 0
         AND bt2.debit IS NOT NULL
         AND LOWER(SUBSTR(bt1.description,1,20)) = LOWER(SUBSTR(bt2.description,1,20))
        JOIN bank_statements bs1 ON bt1.stmt_id = bs1.stmt_id
        JOIN bank_statements bs2 ON bt2.stmt_id = bs2.stmt_id
        WHERE bt1.rowid < bt2.rowid
    """)
    seen = set()
    for row in cur.fetchall():
        key = tuple(sorted([row['stmt1'], row['stmt2']]) + [round(row['amount'], 0)])
        if key in seen:
            continue
        seen.add(key)
        findings.append({
            'category': 'double_payment',
            'pages': [row['pg1'], row['pg2']],
            'document_refs': [row['stmt1'], row['stmt2']],
            'description': (f"Duplicate payment of {row['amount']:.2f} to "
                            f"'{row['description'][:50]}' in {row['stmt1']} and {row['stmt2']}"),
            'reported_value': f"{row['amount']:.2f} (twice)",
            'correct_value': f"{row['amount']:.2f} (once)",
        })
    return findings


def check_ifsc_mismatch(conn) -> list[dict]:
    """Bank IFSC on invoice doesn't match the vendor's registered IFSC in Vendor Master."""
    findings = []
    cur = conn.cursor()

    cur.execute("""
        SELECT i.invoice_no, i.vendor_gstin, i.vendor_ifsc,
               vm.ifsc AS master_ifsc, vm.name AS master_name,
               i.page_start, i.page_end
        FROM invoices i
        JOIN vendor_master vm ON i.vendor_gstin = vm.gstin
        WHERE i.vendor_ifsc IS NOT NULL
          AND vm.ifsc IS NOT NULL
          AND i.vendor_ifsc != vm.ifsc
    """)
    for row in cur.fetchall():
        findings.append({
            'category': 'ifsc_mismatch',
            'pages': list(range(row['page_start'], row['page_end'] + 1)),
            'document_refs': [row['invoice_no']],
            'description': (f"IFSC on invoice {row['vendor_ifsc']} ≠ "
                            f"master IFSC {row['master_ifsc']} for {row['master_name']}"),
            'reported_value': row['vendor_ifsc'],
            'correct_value': row['master_ifsc'],
        })
    return findings


def check_duplicate_expense(conn) -> list[dict]:
    """
    Same expense entry in two different reports.
    Matches on: same description prefix (30 chars) + same amount (±1).
    Date and category intentionally relaxed (category may differ as camouflage).
    """
    findings = []
    cur = conn.cursor()

    cur.execute("""
        SELECT e1.report_id AS rep1, e2.report_id AS rep2,
               e1.entry_date, e1.category, e1.description, e1.amount,
               er1.page_start AS pg1, er2.page_start AS pg2
        FROM expense_entries e1
        JOIN expense_entries e2
          ON e1.report_id != e2.report_id
         AND ABS(e1.amount - e2.amount) < 1.0
         AND LOWER(SUBSTR(e1.description,1,30)) = LOWER(SUBSTR(e2.description,1,30))
        JOIN expense_reports er1 ON e1.report_id = er1.report_id
        JOIN expense_reports er2 ON e2.report_id = er2.report_id
        WHERE e1.rowid < e2.rowid
          AND e1.amount > 100
    """)
    seen = set()
    for row in cur.fetchall():
        key = tuple(sorted([row['rep1'], row['rep2']]) + [row['entry_date'], round(row['amount'], 0)])
        if key in seen:
            continue
        seen.add(key)
        findings.append({
            'category': 'duplicate_expense',
            'pages': [row['pg1'], row['pg2']],
            'document_refs': [row['rep1'], row['rep2']],
            'description': (f"Expense '{row['description'][:50]}' on {row['entry_date']} "
                            f"({row['amount']:.2f}) in both {row['rep1']} and {row['rep2']}"),
            'reported_value': f"{row['amount']:.2f} (twice)",
            'correct_value': f"{row['amount']:.2f} (once)",
        })
    return findings


def check_date_cascade(conn) -> list[dict]:
    """
    Invoice date is before its own PO date (can't invoice before PO exists).
    Conservative: only flag when gap <= 30 days (to avoid retroactive framework POs).
    """
    findings = []
    cur = conn.cursor()
    cur.execute("""
        SELECT i.invoice_no, i.po_ref,
               i.date AS inv_date, po.date AS po_date,
               julianday(po.date) - julianday(i.date) AS gap_days,
               i.page_start, i.page_end
        FROM invoices i
        JOIN purchase_orders po ON i.po_ref = po.po_no
        WHERE i.date IS NOT NULL AND po.date IS NOT NULL
          AND i.date < po.date
          AND julianday(po.date) - julianday(i.date) <= 30
    """)
    for row in cur.fetchall():
        findings.append({
            'category': 'date_cascade',
            'pages': [row['page_start']],
            'document_refs': [row['invoice_no'], row['po_ref']],
            'description': (f"Invoice date {row['inv_date']} is before "
                            f"PO date {row['po_date']} (gap: {int(row['gap_days'])} days)"),
            'reported_value': row['inv_date'],
            'correct_value': f">= {row['po_date']}",
        })
    return findings


def check_gstin_state_mismatch(conn) -> list[dict]:
    """
    First 2 digits of GSTIN (state code) don't match vendor's address state.
    Matches invoice vendor by name (since GSTIN on invoice may be wrong).
    """
    findings = []
    cur = conn.cursor()

    cur.execute("SELECT * FROM vendor_master")
    vendors = [dict(r) for r in cur.fetchall()]
    if not vendors:
        return findings

    cur.execute("""
        SELECT invoice_no, vendor_name, vendor_gstin, page_start, page_end
        FROM invoices
        WHERE vendor_gstin IS NOT NULL AND vendor_name IS NOT NULL
    """)
    cur2 = conn.cursor()
    for row in cur.fetchall():
        inv_gstin = row['vendor_gstin']
        inv_name  = row['vendor_name']
        state_code = utils.gstin_state_code(inv_gstin)
        if not state_code:
            continue

        # Try to find the vendor in master by GSTIN first (exact), then by name (fuzzy)
        master_vendor = None
        cur2.execute("SELECT * FROM vendor_master WHERE gstin = ?", (inv_gstin,))
        vm = cur2.fetchone()
        if vm:
            master_vendor = dict(vm)
        else:
            # GSTIN doesn't match — try name lookup to see if vendor exists with different GSTIN
            master_vendor = utils.find_vendor_match(inv_name, vendors, threshold=0.80)

        if not master_vendor:
            continue

        # Check: does the invoice GSTIN state code match the vendor's registered state?
        expected_code = utils.state_name_to_code(master_vendor['state'])
        if expected_code and expected_code != state_code:
            state_name    = utils.STATE_CODES.get(state_code, state_code)
            expected_state = master_vendor['state']
            findings.append({
                'category': 'gstin_state_mismatch',
                'pages': [row['page_start']],
                'document_refs': [row['invoice_no']],
                'description': (f"GSTIN {inv_gstin} has state code {state_code} ({state_name}) "
                                f"but vendor '{master_vendor['name']}' is in {expected_state} "
                                f"(expected code {expected_code})"),
                'reported_value': f"{inv_gstin[:2]} ({state_name})",
                'correct_value': f"{expected_code} ({expected_state})",
            })

    return findings


# ─── EVIL NEEDLES ──────────────────────────────────────────────────────────────

def check_quantity_accumulation(conn) -> list[dict]:
    """
    Sum of quantities across invoices against a PO exceeds PO qty by 20%+.
    Compares per line item description.
    """
    findings = []
    cur = conn.cursor()

    # Group at PO level: find all (po_ref, description, rate) combos that over-invoice,
    # then collapse to one finding per PO (same invoice refs → dedup handles the rest).
    cur.execute("""
        SELECT i.po_ref,
               GROUP_CONCAT(DISTINCT ili.description) AS descriptions,
               SUM(ili.qty)  AS total_inv_qty,
               MAX(pli.qty)  AS po_qty,
               GROUP_CONCAT(DISTINCT i.invoice_no) AS invoice_list,
               MIN(i.page_start) AS page_start
        FROM invoices i
        JOIN invoice_line_items ili ON i.invoice_no = ili.invoice_no
        JOIN purchase_orders po      ON i.po_ref = po.po_no
        JOIN po_line_items pli       ON pli.po_no = po.po_no
                                    AND pli.description = ili.description
                                    AND ABS(pli.rate - ili.rate) < 0.05
        WHERE i.po_ref IS NOT NULL
          AND ili.qty  IS NOT NULL
          AND pli.qty  IS NOT NULL
        GROUP BY i.po_ref, ili.description, ROUND(ili.rate, 2)
        HAVING SUM(ili.qty) > MAX(pli.qty) * 1.20
           AND COUNT(DISTINCT i.invoice_no) >= 2
    """)
    seen = set()
    for row in cur.fetchall():
        desc = row['descriptions'].split(',')[0]
        key = (row['po_ref'], desc)
        if key in seen:
            continue
        seen.add(key)
        invoices = row['invoice_list'].split(',')
        
        # Collect all pages for invoices and PO
        all_pages = set()
        if row['page_start']: 
            all_pages.add(row['page_start'])
        
        # Get pages for all involved invoices
        placeholders = ','.join(['?'] * len(invoices))
        p_cur = conn.execute(f"SELECT page_start, page_end FROM invoices WHERE invoice_no IN ({placeholders})", invoices)
        for p_row in p_cur.fetchall():
            for p in range(p_row['page_start'], p_row['page_end'] + 1):
                all_pages.add(p)

        findings.append({
            'category': 'quantity_accumulation',
            'pages': sorted(list(all_pages)),
            'document_refs': invoices + [row['po_ref']],
            'description': (f"Total qty {row['total_inv_qty']:.2f} invoiced for "
                            f"'{desc[:50]}' exceeds "
                            f"PO qty {row['po_qty']:.2f} by >20%"),
            'reported_value': f"{row['total_inv_qty']:.2f}",
            'correct_value': f"{row['po_qty']:.2f}",
        })
    return findings


def check_price_escalation(conn) -> list[dict]:
    """All invoices against a PO charge rates exceeding the contracted PO rate."""
    findings = []
    cur = conn.cursor()

    cur.execute("""
        SELECT i.po_ref,
               ili.description,
               COUNT(DISTINCT i.invoice_no) AS inv_count,
               MIN(ili.rate)   AS min_inv_rate,
               MAX(ili.rate)   AS max_inv_rate,
               pli.rate        AS po_rate,
               GROUP_CONCAT(DISTINCT i.invoice_no) AS invoice_list,
               MIN(i.page_start) AS page_start
        FROM invoices i
        JOIN invoice_line_items ili ON i.invoice_no = ili.invoice_no
        JOIN purchase_orders po      ON i.po_ref = po.po_no
        JOIN po_line_items pli       ON pli.po_no = po.po_no
                                    AND pli.description = ili.description
        WHERE i.po_ref IS NOT NULL
          AND ili.rate IS NOT NULL AND pli.rate IS NOT NULL AND pli.rate > 0
        GROUP BY i.po_ref, ili.description
        HAVING inv_count >= 2
           AND min_inv_rate > po_rate * 1.01
    """)
    seen = set()
    for row in cur.fetchall():
        invoices = row['invoice_list'].split(',')
        
        # Collect all pages for invoices and PO
        all_pages = set()
        p_cur = conn.execute("SELECT page_start, page_end FROM purchase_orders WHERE po_no = ?", (row['po_ref'],))
        po_p = p_cur.fetchone()
        if po_p:
            for p in range(po_p['page_start'], po_p['page_end'] + 1):
                all_pages.add(p)
        
        placeholders = ','.join(['?'] * len(invoices))
        i_cur = conn.execute(f"SELECT page_start, page_end FROM invoices WHERE invoice_no IN ({placeholders})", invoices)
        for i_row in i_cur.fetchall():
            for p in range(i_row['page_start'], i_row['page_end'] + 1):
                all_pages.add(p)

        # Get all escalated rates
        r_cur = conn.execute(f"""
            SELECT ili.rate 
            FROM invoice_line_items ili 
            JOIN invoices i ON ili.invoice_no = i.invoice_no 
            WHERE i.po_ref = ? AND ili.description = ?
        """, (row['po_ref'], row['description']))
        rates = sorted(list({f"{r[0]:.2f}" for r in r_cur.fetchall()}))

        findings.append({
            'category': 'price_escalation',
            'pages': sorted(list(all_pages)),
            'document_refs': invoices + [row['po_ref']],
            'description': (f"Rates for '{row['description'][:50]}' in invoices "
                            f"({', '.join(rates)}) exceed PO rate {row['po_rate']:.2f}"),
            'reported_value': ", ".join(rates),
            'correct_value': f"{row['po_rate']:.2f}",
        })
    return findings


def check_balance_drift(conn) -> list[dict]:
    """Bank statement opening balance ≠ previous month's closing balance."""
    findings = []
    cur = conn.cursor()

    # Get all statements ordered by period
    cur.execute("""
        SELECT stmt_id, account_name, account_no, period_start, period_end,
               opening_balance, closing_balance, page_start
        FROM bank_statements
        WHERE period_start IS NOT NULL AND period_end IS NOT NULL
        ORDER BY account_no, period_start
    """)
    rows = cur.fetchall()

    # Group by account number (more reliable than name which may be truncated)
    accounts: dict[str, list] = defaultdict(list)
    for row in rows:
        key = row['account_no'] or row['account_name'] or row['stmt_id']
        accounts[key].append(row)

    for account, stmts in accounts.items():
        for i in range(1, len(stmts)):
            prev = stmts[i - 1]
            curr = stmts[i]
            if prev['closing_balance'] is None or curr['opening_balance'] is None:
                continue
            if abs(prev['closing_balance'] - curr['opening_balance']) > 0.10:
                # Collect pages for both statements
                all_pages = set()
                for p in range(prev['page_start'], prev['page_start'] + 2): # statements are usually 2pgs
                    all_pages.add(p)
                for p in range(curr['page_start'], curr['page_start'] + 2):
                    all_pages.add(p)
                
                findings.append({
                    'category': 'balance_drift',
                    'pages': sorted(list(all_pages)),
                    'document_refs': [curr['stmt_id'], prev['stmt_id']],
                    'description': (f"Opening balance {curr['opening_balance']:.2f} of "
                                    f"{curr['stmt_id']} ≠ closing balance "
                                    f"{prev['closing_balance']:.2f} of {prev['stmt_id']}"),
                    'reported_value': f"{curr['opening_balance']:.2f}",
                    'correct_value': f"{prev['closing_balance']:.2f}",
                })
    return findings


def check_circular_reference(conn) -> list[dict]:
    """
    Credit/debit notes form a loop (A→B→C→A) — none traces back to a real invoice.
    Build a directed graph of original_ref relationships.
    """
    findings = []
    cur = conn.cursor()

    # Build graph: document → original_ref
    edges: dict[str, str] = {}
    cur.execute("SELECT cn_no, original_ref FROM credit_notes WHERE original_ref IS NOT NULL")
    for row in cur.fetchall():
        edges[row['cn_no']] = row['original_ref']

    cur.execute("SELECT dn_no, original_ref FROM debit_notes WHERE original_ref IS NOT NULL")
    for row in cur.fetchall():
        edges[row['dn_no']] = row['original_ref']

    # Collect all valid invoice/PO references
    cur.execute("SELECT invoice_no FROM invoices")
    valid_refs = {row[0] for row in cur.fetchall()}
    cur.execute("SELECT po_no FROM purchase_orders")
    valid_refs |= {row[0] for row in cur.fetchall()}

    # Detect cycles using DFS
    def find_cycle(start: str) -> list[str] | None:
        visited: dict[str, int] = {}
        path = [start]
        node = start
        for _ in range(20):  # max chain length
            nxt = edges.get(node)
            if nxt is None:
                return None  # chain ends at a real/external doc
            if nxt in valid_refs:
                return None  # resolves to real invoice — not circular
            if nxt in visited:
                # Found a cycle
                cycle_start = path.index(nxt) if nxt in path else 0
                return path[cycle_start:]
            visited[nxt] = len(path)
            path.append(nxt)
            node = nxt
        return None

    reported_cycles: set[frozenset] = set()
    for doc in list(edges.keys()):
        cycle = find_cycle(doc)
        if cycle and len(cycle) >= 2:
            key = frozenset(cycle)
            if key not in reported_cycles:
                reported_cycles.add(key)
                # Get page numbers
                pgs = []
                for d in cycle:
                    cur.execute("SELECT page_start FROM credit_notes WHERE cn_no = ?", (d,))
                    r = cur.fetchone()
                    if r:
                        pgs.append(r[0])
                    else:
                        cur.execute("SELECT page_start FROM debit_notes WHERE dn_no = ?", (d,))
                        r = cur.fetchone()
                        if r:
                            pgs.append(r[0])
                findings.append({
                    'category': 'circular_reference',
                    'pages': pgs,
                    'document_refs': list(cycle),
                    'description': (f"Circular reference chain: "
                                    f"{' → '.join(cycle)} → {cycle[0]}"),
                    'reported_value': ' → '.join(cycle),
                    'correct_value': 'Should trace back to a real invoice',
                })
    return findings


def check_triple_expense_claim(conn) -> list[dict]:
    """Same hotel stay claimed in 3 different expense reports."""
    findings = []
    cur = conn.cursor()

    # Hotel stays: category contains 'Hotel' or 'Accommodation'
    cur.execute("""
        SELECT e1.report_id AS r1, e2.report_id AS r2, e3.report_id AS r3,
               e1.entry_date, e1.description, e1.amount,
               er1.page_start AS pg1, er2.page_start AS pg2, er3.page_start AS pg3
        FROM expense_entries e1
        JOIN expense_entries e2
          ON e2.report_id > e1.report_id
         AND ABS(e1.amount - e2.amount) < 1.0
         AND LOWER(SUBSTR(e1.description,1,25)) = LOWER(SUBSTR(e2.description,1,25))
        JOIN expense_entries e3
          ON e3.report_id > e2.report_id
         AND ABS(e1.amount - e3.amount) < 1.0
         AND LOWER(SUBSTR(e1.description,1,25)) = LOWER(SUBSTR(e3.description,1,25))
        JOIN expense_reports er1 ON e1.report_id = er1.report_id
        JOIN expense_reports er2 ON e2.report_id = er2.report_id
        JOIN expense_reports er3 ON e3.report_id = er3.report_id
        WHERE (LOWER(e1.category) LIKE '%hotel%'
            OR LOWER(e1.category) LIKE '%accommodation%'
            OR LOWER(e1.description) LIKE '%hotel%'
            OR LOWER(e1.description) LIKE '%sheraton%'
            OR LOWER(e1.description) LIKE '%hilton%'
            OR LOWER(e1.description) LIKE '%marriott%'
            OR LOWER(e1.description) LIKE '%taj %'
            OR LOWER(e1.description) LIKE '%oberoi%'
            OR LOWER(e1.description) LIKE '%night%')
          AND e1.amount > 100
    """)
    seen = set()
    for row in cur.fetchall():
        key = tuple(sorted([row['r1'], row['r2'], row['r3']]) + [row['entry_date'], round(row['amount'], 0)])
        if key in seen:
            continue
        seen.add(key)
        findings.append({
            'category': 'triple_expense_claim',
            'pages': [row['pg1'], row['pg2'], row['pg3']],
            'document_refs': [row['r1'], row['r2'], row['r3']],
            'description': (f"Hotel stay '{row['description'][:50]}' on {row['entry_date']} "
                            f"({row['amount']:.2f}) claimed in 3 reports: "
                            f"{row['r1']}, {row['r2']}, {row['r3']}"),
            'reported_value': f"{row['amount']:.2f} × 3",
            'correct_value': f"{row['amount']:.2f} × 1",
        })
    return findings


def check_employee_id_collision(conn) -> list[dict]:
    """Same Employee ID used by two different employees across expense reports."""
    findings = []
    cur = conn.cursor()

    cur.execute("""
        SELECT employee_id,
               GROUP_CONCAT(DISTINCT employee_name) AS names,
               GROUP_CONCAT(DISTINCT report_id) AS reports,
               COUNT(DISTINCT employee_name) AS name_count,
               MIN(page_start) AS page_start
        FROM expense_reports
        WHERE employee_id IS NOT NULL AND employee_name IS NOT NULL
        GROUP BY employee_id
        HAVING name_count > 1
    """)
    for row in cur.fetchall():
        reports = row['reports'].split(',')
        findings.append({
            'category': 'employee_id_collision',
            'pages': [row['page_start']],
            'document_refs': reports[:4],  # limit to first 4
            'description': (f"Employee ID {row['employee_id']} used by "
                            f"multiple employees: {row['names']}"),
            'reported_value': row['employee_id'],
            'correct_value': f"Unique ID per employee",
        })
    return findings


def check_fake_vendor(conn) -> list[dict]:
    """Invoice from a vendor whose GSTIN/name doesn't exist in the Vendor Master."""
    findings = []
    cur = conn.cursor()

    cur.execute("SELECT name, gstin FROM vendor_master")
    master_gstins = {row['gstin'] for row in cur.fetchall()}
    cur.execute("SELECT * FROM vendor_master")
    all_vendors = [dict(r) for r in cur.fetchall()]

    cur.execute("""
        SELECT invoice_no, vendor_name, vendor_gstin, page_start, page_end
        FROM invoices
        WHERE vendor_gstin IS NOT NULL
    """)
    for row in cur.fetchall():
        gstin = row['vendor_gstin']
        name  = row['vendor_name'] or ''

        if gstin not in master_gstins:
            # Double-check: if name matches EXACTLY or very strongly, skip.
            # This is likely a gstin_state_mismatch or wrong GSTIN on a real vendor.
            name_match = utils.find_vendor_match(name, all_vendors, threshold=0.95)
            if name_match:
                continue

            findings.append({
                'category': 'fake_vendor',
                'pages': list(range(row['page_start'], row['page_end'] + 1)),
                'document_refs': [row['invoice_no']],
                'description': (f"Vendor '{name}' (GSTIN: {gstin}) "
                                f"not found in Vendor Master"),
                'reported_value': gstin,
                'correct_value': 'Not in Vendor Master',
            })
    return findings


def check_phantom_po_reference(conn) -> list[dict]:
    """Invoice cites a PO number that doesn't exist anywhere in the dataset."""
    findings = []
    cur = conn.cursor()

    cur.execute("SELECT po_no FROM purchase_orders")
    known_pos = {row[0] for row in cur.fetchall()}

    cur.execute("""
        SELECT invoice_no, po_ref, page_start, page_end
        FROM invoices
        WHERE po_ref IS NOT NULL
    """)
    for row in cur.fetchall():
        if row['po_ref'] not in known_pos:
            findings.append({
                'category': 'phantom_po_reference',
                'pages': list(range(row['page_start'], row['page_end'] + 1)),
                'document_refs': [row['invoice_no'], row['po_ref']],
                'description': (f"Invoice references PO {row['po_ref']} "
                                f"which does not exist in the dataset"),
                'reported_value': row['po_ref'],
                'correct_value': 'Valid PO required',
            })
    return findings


# ─── Master runner ─────────────────────────────────────────────────────────────

VALIDATORS = [
    # Easy
    ('arithmetic_error',       check_arithmetic_error,     1),
    ('billing_typo',           check_billing_typo,         1),
    ('duplicate_line_item',    check_duplicate_line_item,  1),
    ('invalid_date',           check_invalid_date,         1),
    ('wrong_tax_rate',         check_wrong_tax_rate,       1),
    # Medium
    ('po_invoice_mismatch',    check_po_invoice_mismatch,  3),
    ('vendor_name_typo',       check_vendor_name_typo,     3),
    ('double_payment',         check_double_payment,       3),
    ('ifsc_mismatch',          check_ifsc_mismatch,        3),
    ('duplicate_expense',      check_duplicate_expense,    3),
    ('date_cascade',           check_date_cascade,         3),
    ('gstin_state_mismatch',   check_gstin_state_mismatch, 3),
    # Evil
    ('quantity_accumulation',  check_quantity_accumulation, 7),
    ('price_escalation',       check_price_escalation,      7),
    ('balance_drift',          check_balance_drift,         7),
    ('circular_reference',     check_circular_reference,    7),
    ('triple_expense_claim',   check_triple_expense_claim,  7),
    ('employee_id_collision',  check_employee_id_collision, 7),
    ('fake_vendor',            check_fake_vendor,           7),
    ('phantom_po_reference',   check_phantom_po_reference,  7),
]


def run_all_validations(db_path: str = db_setup.DB_PATH) -> list[dict]:
    conn = get_conn(db_path)
    all_findings = []
    for name, fn, weight in VALIDATORS:
        try:
            results = fn(conn)
            print(f"  [{weight}pt] {name}: {len(results)} findings")
            all_findings.extend(results)
        except Exception as e:
            print(f"  [ERROR] {name}: {e}")
    conn.close()
    return all_findings


if __name__ == '__main__':
    findings = run_all_validations()
    print(f"\nTotal raw findings: {len(findings)}")
