"""
Ingestion engine: parse each document segment's text and insert into SQLite.
Uses only pypdf text extraction — no API calls.
"""
import json
import os
import re
import pypdf
from pathlib import Path

import db_setup
import utils

PDF_PATH = os.path.join(os.path.dirname(__file__), 'gauntlet.pdf')
SEGMENTS_JSON = os.path.join(os.path.dirname(__file__), 'segments.json')


# ── Helpers ───────────────────────────────────────────────────────────────────

def get_page_texts(reader: pypdf.PdfReader, page_start: int, page_end: int) -> list[str]:
    """Return list of raw text strings for pages page_start..page_end (1-indexed, inclusive)."""
    return [reader.pages[i - 1].extract_text() or '' for i in range(page_start, page_end + 1)]


def combined_text(texts: list[str]) -> str:
    return '\n'.join(texts)


def lines_of(text: str) -> list[str]:
    return [l.strip() for l in text.split('\n')]


def after_label(lines: list[str], label: str) -> str | None:
    """Value on the line immediately after a label (case-insensitive)."""
    for i, line in enumerate(lines):
        if re.match(r'\s*' + re.escape(label) + r'\s*:?\s*$', line.strip(), re.IGNORECASE):
            if i + 1 < len(lines):
                val = lines[i + 1].strip()
                if val:
                    return val
    return None


def amount_before_label(lines: list[str], label: str) -> float | None:
    """In invoice totals, the value appears on the line BEFORE the label."""
    for i, line in enumerate(lines):
        if re.match(r'\s*' + re.escape(label) + r'\s*:?\s*$', line.strip(), re.IGNORECASE):
            if i > 0:
                return utils.parse_amount(lines[i - 1])
    return None


# ── Vendor Master parser ──────────────────────────────────────────────────────

def parse_vendor_master(texts: list[str]) -> list[dict]:
    """
    Parse Vendor Master pages (3-4). Table format:
    # | Vendor Name | GSTIN | State | Bank | IFSC
    """
    vendors = []
    full_text = combined_text(texts)
    lines = lines_of(full_text)

    i = 0
    while i < len(lines):
        line = lines[i]
        # Row starts with a number followed by the vendor name on next line(s)
        m = re.match(r'^(\d{1,2})$', line)
        if m:
            seq = int(m.group(1))
            # Next lines: name, gstin, state, bank (possibly truncated), ifsc
            try:
                name  = lines[i + 1].strip() if i + 1 < len(lines) else ''
                gstin = lines[i + 2].strip() if i + 2 < len(lines) else ''
                state = lines[i + 3].strip() if i + 3 < len(lines) else ''
                bank  = lines[i + 4].strip() if i + 4 < len(lines) else ''
                ifsc  = lines[i + 5].strip() if i + 5 < len(lines) else ''

                # Validate: GSTIN is 15 chars, IFSC is ~11 chars starting with letters
                if re.match(r'^[0-9A-Z]{15}$', gstin) and re.match(r'^[A-Z]{4}', ifsc):
                    vendors.append({
                        'seq_no': seq,
                        'name': name,
                        'gstin': gstin,
                        'state': state,
                        'bank': bank,
                        'ifsc': ifsc,
                    })
                    i += 6
                    continue
            except IndexError:
                pass
        i += 1

    return vendors


# ── Tax Invoice parser ────────────────────────────────────────────────────────

def parse_invoice(texts: list[str], page_start: int, page_end: int) -> dict | None:
    """Parse a TAX INVOICE segment."""
    full = combined_text(texts)
    ls = lines_of(full)

    invoice_no = after_label(ls, 'Invoice No') or after_label(ls, 'Invoice Number')
    if not invoice_no:
        return None

    date_raw    = after_label(ls, 'Date')
    po_ref      = after_label(ls, 'PO Reference') or after_label(ls, 'PO No')
    vendor_name = after_label(ls, 'Name')
    vendor_gstin= after_label(ls, 'GSTIN')
    vendor_ifsc = after_label(ls, 'IFSC')

    # Totals (value appears BEFORE its label in extracted text)
    subtotal    = amount_before_label(ls, 'Subtotal')
    cgst        = amount_before_label(ls, 'CGST')
    sgst        = amount_before_label(ls, 'SGST')
    igst        = amount_before_label(ls, 'IGST')
    grand_total = amount_before_label(ls, 'GRAND TOTAL')

    # Parse line items
    line_items = parse_line_items(full, doc_ref=invoice_no)

    return {
        'invoice_no': invoice_no,
        'po_ref': po_ref,
        'date': utils.parse_date(date_raw) if date_raw else None,
        'vendor_name': vendor_name,
        'vendor_gstin': vendor_gstin,
        'vendor_ifsc': vendor_ifsc,
        'subtotal': subtotal,
        'cgst': cgst,
        'sgst': sgst,
        'igst': igst,
        'grand_total': grand_total,
        'page_start': page_start,
        'page_end': page_end,
        'line_items': line_items,
    }


# ── Purchase Order parser ─────────────────────────────────────────────────────

def parse_po(texts: list[str], page_start: int, page_end: int) -> dict | None:
    """Parse a PURCHASE ORDER segment."""
    full = combined_text(texts)
    ls = lines_of(full)

    po_no = after_label(ls, 'PO Number') or after_label(ls, 'PO No')
    if not po_no:
        return None

    date_raw       = after_label(ls, 'Date')
    delivery_date  = after_label(ls, 'Delivery Date')
    payment_terms  = after_label(ls, 'Payment Terms')
    vendor_name    = after_label(ls, 'Name')
    vendor_gstin   = after_label(ls, 'GSTIN')

    subtotal = amount_before_label(ls, 'Subtotal')
    gst      = amount_before_label(ls, 'GST')
    total    = amount_before_label(ls, 'TOTAL')

    line_items = parse_line_items(full, doc_ref=po_no)

    return {
        'po_no': po_no,
        'date': utils.parse_date(date_raw) if date_raw else None,
        'delivery_date': utils.parse_date(delivery_date) if delivery_date else None,
        'payment_terms': payment_terms,
        'vendor_name': vendor_name,
        'vendor_gstin': vendor_gstin,
        'subtotal': subtotal,
        'gst': gst,
        'total': total,
        'page_start': page_start,
        'page_end': page_end,
        'line_items': line_items,
    }


# ── Line item parser (shared by invoices and POs) ────────────────────────────

def parse_line_items(full_text: str, doc_ref: str) -> list[dict]:
    """
    Parse line items from invoice or PO text.
    Format (each field on its own line after column headers):
        [line_num]
        [description]
        [hsn]
        [qty]
        [unit]
        [rate (with ■)]
        [amount (with ■)]
    """
    items = []
    lines = full_text.split('\n')

    # Find the LINE ITEMS / ORDER ITEMS section
    start_idx = None
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped in ('LINE ITEMS', 'ORDER ITEMS'):
            start_idx = i
            break
    if start_idx is None:
        return items

    # Skip the column header row(s): # Description HSN Qty Unit Rate Amount
    idx = start_idx + 1
    # Skip lines that are header text
    header_words = {'#', 'description', 'hsn', 'qty', 'unit', 'rate', 'amount'}
    while idx < len(lines) and lines[idx].strip().lower() in header_words:
        idx += 1

    # Now parse rows
    # Each row: line_num, description, hsn, qty, unit, rate, amount
    i = idx
    while i < len(lines):
        line = lines[i].strip()

        # Stop at totals section (value appears BEFORE the label)
        if re.match(r'Subtotal\s*:?\s*$', line, re.IGNORECASE):
            break
        # Stop at bank details (end of last page)
        if re.match(r'BANK DETAILS FOR PAYMENT', line, re.IGNORECASE):
            break
        # NOTE: Do NOT stop at legal boilerplate (it appears between pages in multi-page invoices)

        # Check if this line is a line number (integer)
        m = re.match(r'^(\d{1,3})$', line)
        if m:
            line_num = int(m.group(1))
            try:
                description = lines[i + 1].strip()
                hsn_val     = lines[i + 2].strip()
                qty_str     = lines[i + 3].strip()
                unit_val    = lines[i + 4].strip()
                rate_str    = lines[i + 5].strip()
                amount_str  = lines[i + 6].strip()
            except IndexError:
                break

            # Validate: qty should be numeric, rate/amount start with ■ or digit
            qty = utils.parse_amount(qty_str)
            rate = utils.parse_amount(rate_str)
            amount = utils.parse_amount(amount_str)

            if qty is not None and rate is not None and amount is not None:
                # HSN should be alphanumeric (not a unit like "Hrs" or amount)
                if re.match(r'^[0-9A-Z]+$', hsn_val, re.IGNORECASE):
                    items.append({
                        'line_num': line_num,
                        'description': description,
                        'hsn': hsn_val,
                        'qty': qty,
                        'unit': unit_val,
                        'rate': rate,
                        'amount': amount,
                    })
                    i += 7
                    continue
        i += 1

    return items


# ── Bank Statement parser ─────────────────────────────────────────────────────

def parse_bank_statement(texts: list[str], page_start: int, page_end: int) -> dict | None:
    """Parse a BANK STATEMENT segment."""
    full = combined_text(texts)
    ls = lines_of(full)

    stmt_id      = after_label(ls, 'Statement ID') or after_label(ls, 'Statement No')
    if not stmt_id:
        return None

    account_name = after_label(ls, 'Account')
    account_no   = after_label(ls, 'Account No')
    bank         = after_label(ls, 'Bank')
    ifsc         = after_label(ls, 'IFSC')
    period_raw   = after_label(ls, 'Period')

    opening_bal  = after_label(ls, 'Opening Balance')
    closing_bal  = after_label(ls, 'Closing Balance')

    # Parse period: "01/01/2025 to 31/01/2025"
    period_start, period_end = None, None
    if period_raw:
        m = re.match(r'(\d{2}/\d{2}/\d{4})\s+to\s+(\d{2}/\d{2}/\d{4})', period_raw)
        if m:
            period_start = utils.parse_date(m.group(1))
            period_end   = utils.parse_date(m.group(2))

    transactions = parse_bank_transactions(full, stmt_id)

    # Compute closing balance from transactions if not directly available
    computed_closing = None
    if transactions:
        last_bal = transactions[-1].get('balance')
        if last_bal is not None:
            computed_closing = last_bal

    return {
        'stmt_id': stmt_id,
        'account_name': account_name,
        'account_no': account_no,
        'bank': bank,
        'ifsc': ifsc,
        'period_start': period_start,
        'period_end': period_end,
        'opening_balance': utils.parse_amount(opening_bal),
        'closing_balance': utils.parse_amount(closing_bal) or computed_closing,
        'page_start': page_start,
        'page_end': page_end,
        'transactions': transactions,
    }


def parse_bank_transactions(full_text: str, stmt_id: str) -> list[dict]:
    """
    Parse transaction rows from bank statement text.
    Row format (each field on its own line):
        [date DD/MM/YYYY]
        [description]
        [type: CASH/CHQ/NEFT/RTGS/IMPS/UPI/EMI...]
        [ref]
        [debit (■X or -)]
        [credit (■X or -)]
        [balance]
    """
    txns = []
    lines = full_text.split('\n')

    # Find TRANSACTIONS section
    start_idx = None
    for i, line in enumerate(lines):
        if line.strip() == 'TRANSACTIONS':
            start_idx = i
            break
    if start_idx is None:
        return txns

    # Skip header row: Date Description Type Ref Debit Credit Balance
    idx = start_idx + 1
    header_words = {'date', 'description', 'type', 'ref', 'debit', 'credit', 'balance'}
    while idx < len(lines) and lines[idx].strip().lower() in header_words:
        idx += 1

    i = idx
    while i < len(lines):
        line = lines[i].strip()

        # Stop at summary / footer
        if re.match(r'(Closing Balance|Place of supply|Certified|For any|Disclaimer)', line, re.IGNORECASE):
            break

        # Transaction rows start with a date DD/MM/YYYY
        m = re.match(r'^(\d{2}/\d{2}/\d{4})$', line)
        if m:
            try:
                txn_date    = utils.parse_date(lines[i].strip())
                description = lines[i + 1].strip()
                txn_type    = lines[i + 2].strip()
                ref         = lines[i + 3].strip()
                debit_str   = lines[i + 4].strip()
                credit_str  = lines[i + 5].strip()
                balance_str = lines[i + 6].strip()
            except IndexError:
                break

            # '-' means zero for debit/credit
            debit  = utils.parse_amount(debit_str)  if debit_str  != '-' else None
            credit = utils.parse_amount(credit_str) if credit_str != '-' else None
            balance = utils.parse_amount(balance_str)

            if txn_date and balance is not None:
                txns.append({
                    'stmt_id': stmt_id,
                    'txn_date': txn_date,
                    'description': description,
                    'txn_type': txn_type,
                    'ref': ref,
                    'debit': debit,
                    'credit': credit,
                    'balance': balance,
                })
                i += 7
                continue
        i += 1

    return txns


# ── Expense Report parser ─────────────────────────────────────────────────────

def parse_expense_report(texts: list[str], page_start: int, page_end: int) -> dict | None:
    """Parse an EXPENSE REPORT segment."""
    full = combined_text(texts)
    ls = lines_of(full)

    report_id     = after_label(ls, 'Report ID') or after_label(ls, 'Report No')
    if not report_id:
        return None

    date_raw      = after_label(ls, 'Date')
    employee_name = after_label(ls, 'Employee')
    employee_id   = after_label(ls, 'Employee ID')
    department    = after_label(ls, 'Department')
    purpose       = after_label(ls, 'Purpose')
    city          = after_label(ls, 'City')
    total_raw     = after_label(ls, 'TOTAL CLAIMED')

    entries = parse_expense_entries(full, report_id)

    return {
        'report_id': report_id,
        'date': utils.parse_date(date_raw) if date_raw else None,
        'employee_name': employee_name,
        'employee_id': employee_id,
        'department': department,
        'purpose': purpose,
        'city': city,
        'total_claimed': utils.parse_amount(total_raw),
        'page_start': page_start,
        'page_end': page_end,
        'entries': entries,
    }


def parse_expense_entries(full_text: str, report_id: str) -> list[dict]:
    """
    Parse expense entries.
    Row format:
        [entry_num]
        [date DD/MM/YYYY]
        [category]
        [description]
        [city]
        [amount ■X]
    """
    entries = []
    lines = full_text.split('\n')

    start_idx = None
    for i, line in enumerate(lines):
        if line.strip() == 'EXPENSE ENTRIES':
            start_idx = i
            break
    if start_idx is None:
        return entries

    idx = start_idx + 1
    header_words = {'#', 'date', 'category', 'description', 'city', 'amount'}
    while idx < len(lines) and lines[idx].strip().lower() in header_words:
        idx += 1

    i = idx
    while i < len(lines):
        line = lines[i].strip()

        if re.match(r'(TOTAL CLAIMED|APPROVAL|Status|Approver|Employee Signature)', line, re.IGNORECASE):
            break

        m = re.match(r'^(\d{1,3})$', line)
        if m:
            try:
                entry_date  = lines[i + 1].strip()
                category    = lines[i + 2].strip()
                description = lines[i + 3].strip()
                city        = lines[i + 4].strip()
                amount_str  = lines[i + 5].strip()
            except IndexError:
                break

            date_parsed = utils.parse_date(entry_date)
            amount = utils.parse_amount(amount_str)

            if date_parsed and amount is not None:
                entries.append({
                    'report_id': report_id,
                    'entry_date': date_parsed,
                    'category': category,
                    'description': description,
                    'city': city,
                    'amount': amount,
                })
                i += 6
                continue
        i += 1

    return entries


# ── Credit/Debit Note parser ──────────────────────────────────────────────────

def parse_credit_note(texts: list[str], page_start: int, page_end: int) -> dict | None:
    full = combined_text(texts)
    ls = lines_of(full)

    cn_no = after_label(ls, 'CREDIT NOTE No') or after_label(ls, 'Credit Note No')
    if not cn_no:
        return None

    date_raw     = after_label(ls, 'Date')
    vendor_name  = after_label(ls, 'Vendor')
    vendor_gstin = after_label(ls, 'GSTIN')
    original_ref = after_label(ls, 'Original Invoice') or after_label(ls, 'Against Invoice')
    reason       = after_label(ls, 'Reason')
    amount_raw   = after_label(ls, 'Amount')

    return {
        'cn_no': cn_no,
        'date': utils.parse_date(date_raw) if date_raw else None,
        'vendor_name': vendor_name,
        'vendor_gstin': vendor_gstin,
        'original_ref': original_ref,
        'reason': reason,
        'amount': utils.parse_amount(amount_raw),
        'page_start': page_start,
        'page_end': page_end,
    }


def parse_debit_note(texts: list[str], page_start: int, page_end: int) -> dict | None:
    full = combined_text(texts)
    ls = lines_of(full)

    dn_no = after_label(ls, 'DEBIT NOTE No') or after_label(ls, 'Debit Note No')
    if not dn_no:
        return None

    date_raw     = after_label(ls, 'Date')
    vendor_name  = after_label(ls, 'Vendor')
    vendor_gstin = after_label(ls, 'GSTIN')
    original_ref = after_label(ls, 'Original Invoice') or after_label(ls, 'Against Invoice')
    reason       = after_label(ls, 'Reason')
    amount_raw   = after_label(ls, 'Amount')

    return {
        'dn_no': dn_no,
        'date': utils.parse_date(date_raw) if date_raw else None,
        'vendor_name': vendor_name,
        'vendor_gstin': vendor_gstin,
        'original_ref': original_ref,
        'reason': reason,
        'amount': utils.parse_amount(amount_raw),
        'page_start': page_start,
        'page_end': page_end,
    }


# ── DB insertion helpers ──────────────────────────────────────────────────────

def insert_vendor_master(conn, vendors: list[dict]):
    cur = conn.cursor()
    cur.execute("DELETE FROM vendor_master")
    for v in vendors:
        cur.execute("""
            INSERT OR REPLACE INTO vendor_master (seq_no, name, gstin, state, bank, ifsc)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (v['seq_no'], v['name'], v['gstin'], v['state'], v['bank'], v['ifsc']))
    conn.commit()
    print(f"  [ingest] Inserted {len(vendors)} vendor master records")


def insert_invoice(conn, inv: dict):
    cur = conn.cursor()
    # Delete existing line items before re-inserting (idempotent re-ingestion)
    cur.execute("DELETE FROM invoice_line_items WHERE invoice_no = ?", (inv['invoice_no'],))
    cur.execute("""
        INSERT OR REPLACE INTO invoices
          (invoice_no, po_ref, date, vendor_name, vendor_gstin, vendor_ifsc,
           subtotal, cgst, sgst, igst, grand_total, page_start, page_end)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, (
        inv['invoice_no'], inv.get('po_ref'), inv.get('date'),
        inv.get('vendor_name'), inv.get('vendor_gstin'), inv.get('vendor_ifsc'),
        inv.get('subtotal'), inv.get('cgst'), inv.get('sgst'), inv.get('igst'),
        inv.get('grand_total'), inv.get('page_start'), inv.get('page_end'),
    ))
    for item in inv.get('line_items', []):
        cur.execute("""
            INSERT INTO invoice_line_items
              (invoice_no, line_num, description, hsn, qty, unit, rate, amount)
            VALUES (?,?,?,?,?,?,?,?)
        """, (
            inv['invoice_no'], item['line_num'], item['description'],
            item['hsn'], item['qty'], item['unit'], item['rate'], item['amount'],
        ))
    conn.commit()


def insert_po(conn, po: dict):
    cur = conn.cursor()
    cur.execute("DELETE FROM po_line_items WHERE po_no = ?", (po['po_no'],))
    cur.execute("""
        INSERT OR REPLACE INTO purchase_orders
          (po_no, date, delivery_date, payment_terms, vendor_name, vendor_gstin,
           subtotal, gst, total, page_start, page_end)
        VALUES (?,?,?,?,?,?,?,?,?,?,?)
    """, (
        po['po_no'], po.get('date'), po.get('delivery_date'), po.get('payment_terms'),
        po.get('vendor_name'), po.get('vendor_gstin'),
        po.get('subtotal'), po.get('gst'), po.get('total'),
        po.get('page_start'), po.get('page_end'),
    ))
    for item in po.get('line_items', []):
        cur.execute("""
            INSERT INTO po_line_items
              (po_no, line_num, description, hsn, qty, unit, rate, amount)
            VALUES (?,?,?,?,?,?,?,?)
        """, (
            po['po_no'], item['line_num'], item['description'],
            item['hsn'], item['qty'], item['unit'], item['rate'], item['amount'],
        ))
    conn.commit()


def insert_bank_statement(conn, bs: dict):
    cur = conn.cursor()
    cur.execute("DELETE FROM bank_transactions WHERE stmt_id = ?", (bs['stmt_id'],))
    cur.execute("""
        INSERT OR REPLACE INTO bank_statements
          (stmt_id, account_name, account_no, bank, ifsc,
           period_start, period_end, opening_balance, closing_balance, page_start, page_end)
        VALUES (?,?,?,?,?,?,?,?,?,?,?)
    """, (
        bs['stmt_id'], bs.get('account_name'), bs.get('account_no'),
        bs.get('bank'), bs.get('ifsc'), bs.get('period_start'), bs.get('period_end'),
        bs.get('opening_balance'), bs.get('closing_balance'),
        bs.get('page_start'), bs.get('page_end'),
    ))
    for t in bs.get('transactions', []):
        cur.execute("""
            INSERT INTO bank_transactions
              (stmt_id, txn_date, description, txn_type, ref, debit, credit, balance)
            VALUES (?,?,?,?,?,?,?,?)
        """, (
            bs['stmt_id'], t['txn_date'], t['description'], t['txn_type'],
            t['ref'], t.get('debit'), t.get('credit'), t.get('balance'),
        ))
    conn.commit()


def insert_expense_report(conn, exp: dict):
    cur = conn.cursor()
    cur.execute("DELETE FROM expense_entries WHERE report_id = ?", (exp['report_id'],))
    cur.execute("""
        INSERT OR REPLACE INTO expense_reports
          (report_id, date, employee_name, employee_id, department,
           purpose, city, total_claimed, page_start, page_end)
        VALUES (?,?,?,?,?,?,?,?,?,?)
    """, (
        exp['report_id'], exp.get('date'), exp.get('employee_name'),
        exp.get('employee_id'), exp.get('department'), exp.get('purpose'),
        exp.get('city'), exp.get('total_claimed'),
        exp.get('page_start'), exp.get('page_end'),
    ))
    for e in exp.get('entries', []):
        cur.execute("""
            INSERT INTO expense_entries
              (report_id, entry_date, category, description, city, amount)
            VALUES (?,?,?,?,?,?)
        """, (
            exp['report_id'], e['entry_date'], e['category'],
            e['description'], e['city'], e['amount'],
        ))
    conn.commit()


def insert_credit_note(conn, cn: dict):
    conn.execute("""
        INSERT OR REPLACE INTO credit_notes
          (cn_no, date, vendor_name, vendor_gstin, original_ref, reason, amount, page_start, page_end)
        VALUES (?,?,?,?,?,?,?,?,?)
    """, (
        cn['cn_no'], cn.get('date'), cn.get('vendor_name'), cn.get('vendor_gstin'),
        cn.get('original_ref'), cn.get('reason'), cn.get('amount'),
        cn.get('page_start'), cn.get('page_end'),
    ))
    conn.commit()


def insert_debit_note(conn, dn: dict):
    conn.execute("""
        INSERT OR REPLACE INTO debit_notes
          (dn_no, date, vendor_name, vendor_gstin, original_ref, reason, amount, page_start, page_end)
        VALUES (?,?,?,?,?,?,?,?,?)
    """, (
        dn['dn_no'], dn.get('date'), dn.get('vendor_name'), dn.get('vendor_gstin'),
        dn.get('original_ref'), dn.get('reason'), dn.get('amount'),
        dn.get('page_start'), dn.get('page_end'),
    ))
    conn.commit()


# ── Main ingestion loop ───────────────────────────────────────────────────────

def run_ingestion(
    segments_path: str = SEGMENTS_JSON,
    pdf_path: str = PDF_PATH,
    db_path: str = db_setup.DB_PATH,
):
    import custom_split

    # Load segments (or re-create them)
    if not os.path.exists(segments_path):
        print("[ingest] segments.json not found, running custom_split...")
        segs = custom_split.split_pdf(pdf_path)
        custom_split.save_segments(segs, segments_path)
    else:
        segs = custom_split.load_segments(segments_path)

    reader = pypdf.PdfReader(pdf_path)
    conn   = db_setup.get_connection(db_path)

    counts = {k: 0 for k in [
        'VENDOR MASTER', 'TAX INVOICE', 'PURCHASE ORDER',
        'BANK STATEMENT', 'EXPENSE REPORT', 'CREDIT NOTE', 'DEBIT NOTE', 'SKIPPED'
    ]}

    for seg in segs:
        dtype      = seg['doc_type']
        ps, pe     = seg['page_start'], seg['page_end']
        texts      = get_page_texts(reader, ps, pe)

        if dtype == 'VENDOR MASTER':
            vendors = parse_vendor_master(texts)
            if vendors:
                insert_vendor_master(conn, vendors)
                counts['VENDOR MASTER'] = len(vendors)

        elif dtype == 'TAX INVOICE':
            inv = parse_invoice(texts, ps, pe)
            if inv:
                insert_invoice(conn, inv)
                counts['TAX INVOICE'] += 1

        elif dtype == 'PURCHASE ORDER':
            po = parse_po(texts, ps, pe)
            if po:
                insert_po(conn, po)
                counts['PURCHASE ORDER'] += 1

        elif dtype == 'BANK STATEMENT':
            bs = parse_bank_statement(texts, ps, pe)
            if bs:
                insert_bank_statement(conn, bs)
                counts['BANK STATEMENT'] += 1

        elif dtype == 'EXPENSE REPORT':
            exp = parse_expense_report(texts, ps, pe)
            if exp:
                insert_expense_report(conn, exp)
                counts['EXPENSE REPORT'] += 1

        elif dtype == 'CREDIT NOTE':
            cn = parse_credit_note(texts, ps, pe)
            if cn:
                insert_credit_note(conn, cn)
                counts['CREDIT NOTE'] += 1

        elif dtype == 'DEBIT NOTE':
            dn = parse_debit_note(texts, ps, pe)
            if dn:
                insert_debit_note(conn, dn)
                counts['DEBIT NOTE'] += 1

        else:
            counts['SKIPPED'] += 1

    conn.close()
    print("[ingest] Ingestion complete:")
    for k, v in counts.items():
        if v:
            print(f"  {k}: {v}")


if __name__ == '__main__':
    run_ingestion()
