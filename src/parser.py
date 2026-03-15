"""
Parse raw document text into structured data models.
Tuned for the actual format found in the gauntlet PDF:
  - Amounts prefixed with 'n' (e.g., n16,500.00) 
  - Vendor info under "VENDOR DETAILS / Name:"
  - Line items: # Description HSN Qty Unit Rate Amount
  - Bank statement with Type and Ref columns
  - Expense report with Category and City columns
"""
import re
from typing import List, Optional
from src.models import (
    Document, DocType, Invoice, LineItem, PurchaseOrder, POLineItem,
    BankStatement, BankTransaction, ExpenseReport, ExpenseItem,
    CreditDebitNote
)


def parse_number(s: str) -> float:
    """Parse a number string handling 'n' prefix, commas, negatives."""
    if not s or s == '-':
        return 0.0
    s = str(s).strip()
    
    # Handle negative with 'n' prefix: -n8,561.19
    negative = False
    if s.startswith('-'):
        negative = True
        s = s[1:]
    
    # Remove 'n' prefix (used instead of ₹ in this PDF)
    s = s.lstrip('n').lstrip('₹').lstrip('$').lstrip('N')
    
    # Remove commas and spaces
    s = s.replace(',', '').replace(' ', '')
    
    # Remove trailing % if present
    s = s.rstrip('%')
    
    try:
        val = float(s)
        return -val if negative else val
    except (ValueError, TypeError):
        return 0.0


def parse_date_str(s: str) -> str:
    if not s:
        return ""
    return s.strip()


def parse_invoice(doc: Document) -> Invoice:
    """Parse invoice from raw text."""
    text = doc.raw_text
    inv = Invoice(doc_id=doc.doc_id, pages=doc.pages, raw_text=text)
    
    # Vendor details should ONLY come from the first page
    first_page_text = text.split('\n\n')[0]
    
    # Extract vendor name: "Name: Mindtree Ltd" under VENDOR section
    name_match = re.search(
        r'(?:VENDOR\s+DETAILS|VENDOR|SELLER|SUPPLIER).*?Name[:\s]+([A-Z][A-Za-z&\s\.\'\-]+(?:Ltd|Limited|Pvt|Inc|Corp|LLP)\.?)',
        first_page_text, re.IGNORECASE | re.DOTALL
    )
    if name_match:
        inv.vendor_name = name_match.group(1).strip()
    else:
        # Fallback: look for "Vendor:" on credit/debit notes
        vm = re.search(r'Vendor[:\s]+([A-Z][A-Za-z&\s\.\'\-]+(?:Ltd|Limited|Pvt|Inc|Corp|LLP)\.?)', first_page_text, re.IGNORECASE)
        if vm:
            inv.vendor_name = vm.group(1).strip()
    
    # Extract all GSTINs from first page
    all_gstins = re.findall(r'([0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][0-9A-Z][A-Z][0-9A-Z])', first_page_text)
    vendor_gstin_match = re.search(r'(?:VENDOR|SELLER|SUPPLIER).*?GSTIN[:\s]*([0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][0-9A-Z][A-Z][0-9A-Z])', first_page_text, re.IGNORECASE | re.DOTALL)
    if vendor_gstin_match:
        inv.vendor_gstin = vendor_gstin_match.group(1)
    elif all_gstins:
        inv.vendor_gstin = all_gstins[0]
    
    # Extract IFSC from bank details section (first page)
    ifsc_match = re.search(r'IFSC[:\s]*([A-Z]{4}\d{7,11})', first_page_text, re.IGNORECASE)
    if ifsc_match:
        inv.vendor_ifsc = ifsc_match.group(1)
    
    # Extract date (prefer first page)
    date_match = re.search(r'Date[:\s]*(\d{1,2}/\d{1,2}/\d{4})', first_page_text, re.IGNORECASE)
    if not date_match:
        date_match = re.search(r'Date[:\s]*(\d{1,2}-\d{1,2}-\d{4})', first_page_text, re.IGNORECASE)
    
    if date_match:
        inv.date = date_match.group(1)
    else:
        # Global fallback if first page missing date
        dg = re.search(r'Date[:\s]*(\d{1,2}[/-]\d{1,2}[/-]\d{4})', text, re.IGNORECASE)
        if dg: inv.date = dg.group(1)
    
    # Extract PO reference (prefer first page)
    po_match = re.search(r'(?:PO\s+Reference|P\.?O\.?\s*(?:No|Number|Ref)?)[:\s]*(PO-\d{4}-\d+)', first_page_text, re.IGNORECASE)
    if po_match:
        inv.po_ref = po_match.group(1)
    else:
        pg = re.search(r'(PO-\d{4}-\d+)', text)
        if pg: inv.po_ref = pg.group(1)
    
    # Extract state from vendor address (first page)
    state_match = re.search(r'Address:.*?,\s*([A-Za-z\s]+?)\s*-\s*\d{6}', first_page_text, re.IGNORECASE)
    if state_match:
        inv.vendor_state = state_match.group(1).strip()
    
    # Extract line items from the table
    inv.line_items = extract_invoice_line_items(text)
    
    # Extract totals - handle 'n' prefix
    subtotal_match = re.search(r'Subtotal[:\s]*n?([\d,]+\.?\d*)', text, re.IGNORECASE)
    if subtotal_match:
        inv.subtotal = parse_number(subtotal_match.group(1))
    
    # Tax: CGST + SGST or IGST
    cgst_match = re.search(r'CGST[:\s]*n?([\d,]+\.?\d*)', text, re.IGNORECASE)
    sgst_match = re.search(r'SGST[:\s]*n?([\d,]+\.?\d*)', text, re.IGNORECASE)
    igst_match = re.search(r'IGST[:\s]*n?([\d,]+\.?\d*)', text, re.IGNORECASE)
    
    if cgst_match and sgst_match:
        inv.tax_total = parse_number(cgst_match.group(1)) + parse_number(sgst_match.group(1))
    elif igst_match:
        inv.tax_total = parse_number(igst_match.group(1))
    else:
        tax_match = re.search(r'(?:Tax|GST)\s*(?:Total)?[:\s]*n?([\d,]+\.?\d*)', text, re.IGNORECASE)
        if tax_match:
            inv.tax_total = parse_number(tax_match.group(1))
    
    grand_match = re.search(r'GRAND\s+TOTAL[:\s]*n?([\d,]+\.?\d*)', text, re.IGNORECASE)
    if grand_match:
        inv.grand_total = parse_number(grand_match.group(1))
    else:
        total_match = re.search(r'(?:Total\s+Amount|Net\s+Payable|Amount\s+Due)[:\s]*n?([\d,]+\.?\d*)', text, re.IGNORECASE)
        if total_match:
            inv.grand_total = parse_number(total_match.group(1))
    
    # Calculate effective tax rate
    if inv.subtotal > 0 and inv.tax_total > 0:
        inv.tax_rate = round((inv.tax_total / inv.subtotal) * 100, 1)
        # Apply to line items if they don't have one
        for item in inv.line_items:
            item.tax_rate = inv.tax_rate
    
    return inv


def extract_invoice_line_items(text: str) -> List[LineItem]:
    """
    Extract line items from invoice.
    Format: # Description HSN Qty Unit Rate Amount
    Supports multi-page invoices with repeated headers.
    """
    items = []
    lines = text.split('\n')
    
    in_items = False
    for line in lines:
        stripped = line.strip()
        
        # Detect start of line items section (can happen multiple times in multi-page doc)
        if re.search(r'^#\s+Description\s+HSN', stripped, re.IGNORECASE):
            in_items = True
            continue
        
        # Detect end of section (be careful not to end on continuation markers)
        if in_items and re.search(r'^(Subtotal|Sub\s*total|GRAND|CGST|SGST|IGST|Tax)', stripped, re.IGNORECASE):
            # However, if it's just 'TOTAL' on a continuation page, it might still have more items later
            # But the 'Continued' header logic should have merged the text.
            in_items = False
            continue
        
        if in_items:
            # Pattern: 1 Description Text 998412 33.00 Unit n16,500.00 n5,44,500.00
            match = re.match(
                r'(\d+)\s+'           # Line number
                r'(.+?)\s+'           # Description  
                r'(\d{4,8})\s+'       # HSN/SAC code
                r'([\d,]+\.?\d*)\s+'  # Qty
                r'(\w+)\s+'           # Unit
                r'n?([\d,]+\.?\d*)\s+'  # Rate
                r'n?([\d,]+\.?\d*)',   # Amount
                stripped
            )
            if match:
                items.append(LineItem(
                    description=match.group(2).strip(),
                    hsn_sac=match.group(3),
                    qty=parse_number(match.group(4)),
                    unit=match.group(5).strip(),
                    rate=parse_number(match.group(6)),
                    amount=parse_number(match.group(7)),
                ))
                # Check for billing typo pattern (Hrs as fraction)
                qty = parse_number(match.group(4))
                if qty in [0.15, 0.30, 0.45]:
                    items[-1].hours = qty
                continue
                continue
            
            # Simplified pattern without unit
            match = re.match(
                r'(\d+)\s+'
                r'(.+?)\s+'
                r'(\d{4,8})\s+'
                r'([\d,]+\.?\d*)\s+'
                r'n?([\d,]+\.?\d*)\s+'
                r'n?([\d,]+\.?\d*)',
                stripped
            )
            if match:
                items.append(LineItem(
                    description=match.group(2).strip(),
                    hsn_sac=match.group(3),
                    qty=parse_number(match.group(4)),
                    rate=parse_number(match.group(5)),
                    amount=parse_number(match.group(6)),
                ))
    
    return items


def parse_purchase_order(doc: Document) -> PurchaseOrder:
    """Parse purchase order from raw text."""
    text = doc.raw_text
    po = PurchaseOrder(doc_id=doc.doc_id, pages=doc.pages, raw_text=text)
    
    # Vendor details strictly from first page
    first_page_text = text.split('\n\n')[0]
    
    # Vendor name
    name_match = re.search(r'VENDOR.*?Name[:\s]+([A-Z][A-Za-z&\s\.\'\-]+(?:Ltd|Limited|Pvt)\.?)', first_page_text, re.IGNORECASE | re.DOTALL)
    if name_match:
        po.vendor_name = name_match.group(1).strip()
    
    # Date
    date_match = re.search(r'Date[:\s]*(\d{1,2}/\d{1,2}/\d{4})', first_page_text, re.IGNORECASE)
    if date_match:
        po.date = date_match.group(1)
    else:
        dg = re.search(r'Date[:\s]*(\d{1,2}/\d{1,2}/\d{4})', text, re.IGNORECASE)
        if dg: po.date = dg.group(1)
    
    # Extract line items (same format as invoice: # Description HSN Qty Unit Rate Amount)
    lines = text.split('\n')
    in_items = False
    for line in lines:
        stripped = line.strip()
        
        if re.search(r'^#\s+Description\s+HSN', stripped, re.IGNORECASE):
            in_items = True
            continue
        
        if in_items and re.search(r'^(Subtotal|Sub\s*total|TOTAL|GST)', stripped, re.IGNORECASE):
            in_items = False
            continue
        
        if in_items:
            match = re.match(
                r'(\d+)\s+(.+?)\s+(\d{4,8})\s+([\d,]+\.?\d*)\s+(\w+)\s+n?([\d,]+\.?\d*)\s+n?([\d,]+\.?\d*)',
                stripped
            )
            if match:
                po.line_items.append(POLineItem(
                    description=match.group(2).strip(),
                    hsn_sac=match.group(3),
                    qty=parse_number(match.group(4)),
                    rate=parse_number(match.group(6)),
                    amount=parse_number(match.group(7)),
                ))
    
    # Total
    total_match = re.search(r'TOTAL[:\s]*n?([\d,]+\.?\d*)', text, re.IGNORECASE)
    if total_match:
        po.total = parse_number(total_match.group(1))
    
    return po


def parse_bank_statement(doc: Document) -> BankStatement:
    """Parse bank statement. Format:
    Date Description Type Ref Debit Credit Balance
    """
    text = doc.raw_text
    bs = BankStatement(doc_id=doc.doc_id, pages=doc.pages, raw_text=text)
    
    # Period
    period_match = re.search(r'Period[:\s]*(.+?)\s+to\s+(.+)', text, re.IGNORECASE)
    if period_match:
        from src.utils import parse_date_to_tuple
        d_tuple = parse_date_to_tuple(period_match.group(1))
        if d_tuple:
            y, m, d = d_tuple
            month_names = ['', 'January', 'February', 'March', 'April', 'May', 'June',
                           'July', 'August', 'September', 'October', 'November', 'December']
            if 1 <= m <= 12:
                bs.month = month_names[m]
            bs.year = str(y)
    
    # Opening balance
    ob_match = re.search(r'Opening\s+Balance[:\s]*-?n?([\d,]+\.?\d*)', text, re.IGNORECASE)
    if ob_match:
        # Check if negative
        neg_match = re.search(r'Opening\s+Balance[:\s]*(?:-|\()', text, re.IGNORECASE)
        val = parse_number(ob_match.group(1))
        if neg_match:
            val = -val
        bs.opening_balance = val
    
    # Closing balance (look at end of document)
    cb_match = re.search(r'Closing\s+Balance[:\s]*-?n?([\d,]+\.?\d*)', text, re.IGNORECASE)
    if cb_match:
        neg_match = re.search(r'Closing\s+Balance[:\s]*(-)', text, re.IGNORECASE)
        val = parse_number(cb_match.group(1))
        if neg_match:
            val = -val
        bs.closing_balance = val
    
    # Account Name/Number
    acc_match = re.search(r'Account\s+(?:Name|Number|No)[:\s]*([\w\s\-]+)', text, re.IGNORECASE)
    if acc_match:
        bs.account_name = acc_match.group(1).strip()
    else:
        # Fallback: look for generic Holder/Name near top
        name_match = re.search(r'Name[:\s]*([A-Z\s]+)', text[:500], re.IGNORECASE)
        if name_match:
            bs.account_name = name_match.group(1).strip()
    
    # Extract transactions
    # Format: DD/MM/YYYY Description Type Ref Debit Credit Balance
    lines = text.split('\n')
    for line in lines:
        stripped = line.strip()
        # Match transaction line
        match = re.match(
            r'(\d{2}/\d{2}/\d{4})\s+'  # Date
            r'(.+?)\s+'                  # Description
            r'(NEFT|RTGS|IMPS|UPI|CHQ|CASH|EMI/LOAN|DD|SWIFT)\s*'  # Type
            r'(\w+)\s+'                  # Ref
            r'(?:n?([\d,]+\.?\d*))?\s*'  # Debit (optional)
            r'(?:-\s*)?'
            r'(?:n?([\d,]+\.?\d*))?\s+'  # Credit (optional)
            r'-?n?([\d,]+\.?\d*)',        # Balance
            stripped
        )
        if match:
            txn = BankTransaction(
                date=match.group(1),
                description=match.group(2).strip(),
                payment_ref=f"{match.group(3)}{match.group(4)}",
                vendor_ref=match.group(4),
            )
            
            # Handle debit/credit - in the format, "-" means empty
            debit_str = match.group(5) or ""
            credit_str = match.group(6) or ""
            balance_str = match.group(7) or ""
            
            txn.debit = parse_number(debit_str)
            txn.credit = parse_number(credit_str)
            txn.balance = parse_number(balance_str)
            
            # Check balance sign
            if '-n' in stripped.split(balance_str)[0][-3:] if balance_str else False:
                txn.balance = -txn.balance
            
            bs.transactions.append(txn)
    
    return bs


def parse_expense_report(doc: Document) -> ExpenseReport:
    """Parse expense report. Format:
    # Date Category Description City Amount
    """
    text = doc.raw_text
    er = ExpenseReport(doc_id=doc.doc_id, pages=doc.pages, raw_text=text)
    
    # Employee name
    emp_match = re.search(r'Employee[:\s]+([A-Z][a-zA-Z\s]+?)(?:\n|$)', text, re.IGNORECASE)
    if emp_match:
        er.employee_name = emp_match.group(1).strip()
    
    # Employee ID
    empid_match = re.search(r'Employee\s+ID[:\s]*(EMP-\d+|\w+)', text, re.IGNORECASE)
    if empid_match:
        er.employee_id = empid_match.group(1).strip()
    
    # Department
    dept_match = re.search(r'Department[:\s]*(\w[\w\s]*?)(?:\n|$)', text, re.IGNORECASE)
    if dept_match:
        er.department = dept_match.group(1).strip()
    
    # Period/Purpose
    purpose_match = re.search(r'Purpose[:\s]*(.+?)(?:\n|$)', text, re.IGNORECASE)
    if purpose_match:
        er.period = purpose_match.group(1).strip()
    
    # Extract expense entries
    lines = text.split('\n')
    in_entries = False
    for line in lines:
        stripped = line.strip()
        
        if re.search(r'^#\s+Date\s+Category', stripped, re.IGNORECASE):
            in_entries = True
            continue
        
        if in_entries and re.search(r'^(TOTAL|Total\s+Claimed|APPROVAL|Status)', stripped, re.IGNORECASE):
            in_entries = False
            continue
        
        if in_entries:
            # Pattern: 1 12/12/2025 Category Description City n6,444.60
            match = re.match(
                r'(\d+)\s+'               # Line number
                r'(\d{2}/\d{2}/\d{4})\s+'  # Date
                r'(.+?)\s+'               # Category + Description (merged, tricky)
                r'([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)\s+'  # City
                r'n?([\d,]+\.?\d*)',       # Amount
                stripped
            )
            if match:
                cat_desc = match.group(3).strip()
                # Try to split category from description
                # Categories: Hotel, Software License, Local Conveyance, Parking, Office Supplies, Meals, etc.
                item = ExpenseItem(
                    date=match.group(2),
                    description=cat_desc,
                    category="",
                    city=match.group(4).strip(),
                    amount=parse_number(match.group(5)),
                )
                
                # Extract hotel name if present
                hotel_match = re.search(
                    r'(Sheraton|Hilton|Marriott|Hyatt|Taj|Oberoi|Leela|Radisson|Novotel|Holiday Inn|ITC|JW|Ritz|Park|Grand|Westin|Crowne)',
                    cat_desc, re.IGNORECASE
                )
                if hotel_match:
                    item.hotel_name = cat_desc
                    item.category = "hotel"
                
                er.line_items.append(item)
    
    # Total
    total_match = re.search(r'TOTAL\s+CLAIMED[:\s]*n?([\d,]+\.?\d*)', text, re.IGNORECASE)
    if total_match:
        er.total = parse_number(total_match.group(1))
    
    return er


def parse_credit_debit_note(doc: Document) -> CreditDebitNote:
    """Parse credit/debit note. Simple single-page format."""
    text = doc.raw_text
    note = CreditDebitNote(
        doc_id=doc.doc_id,
        pages=doc.pages,
        raw_text=text,
        note_type="credit" if doc.doc_type == DocType.CREDIT_NOTE else "debit"
    )
    
    # Vendor
    vendor_match = re.search(r'Vendor[:\s]+([A-Z][A-Za-z&\s\.\'\-]+(?:Ltd|Limited|Pvt)\.?)', text, re.IGNORECASE)
    if vendor_match:
        note.vendor_name = vendor_match.group(1).strip()
    
    # Date
    date_match = re.search(r'Date[:\s]*(\d{1,2}/\d{1,2}/\d{4})', text, re.IGNORECASE)
    if date_match:
        note.date = date_match.group(1)
    
    # GSTIN
    gstin_match = re.search(r'GSTIN[:\s]*([0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][0-9A-Z][A-Z][0-9A-Z])', text, re.IGNORECASE)
    if gstin_match:
        note.vendor_name  # Already captured above
    
    # References - original invoice or note
    ref_match = re.search(r'Original\s+Invoice[:\s]*((?:INV|CN|DN)-\d{4}-\d+)', text, re.IGNORECASE)
    if ref_match:
        note.references.append(ref_match.group(1))
    
    # Also find all document refs in text
    all_refs = re.findall(r'((?:INV|CN|DN)-\d{4}-\d+)', text)
    for ref in all_refs:
        if ref != doc.doc_id and ref not in note.references:
            note.references.append(ref)
    
    # Amount
    amount_match = re.search(r'Amount[:\s]*\n?n?([\d,]+\.?\d*)', text, re.IGNORECASE)
    if amount_match:
        note.amount = parse_number(amount_match.group(1))
    
    # Reason
    reason_match = re.search(r'Reason[:\s]*(.+?)(?:\n|$)', text, re.IGNORECASE)
    if reason_match:
        note.reason = reason_match.group(1).strip()
    
    return note


def parse_all_documents(documents: List[Document]) -> List[Document]:
    """Parse all documents into structured data."""
    print("🔍 Parsing documents into structured data...")
    
    parsers = {
        DocType.INVOICE: parse_invoice,
        DocType.PURCHASE_ORDER: parse_purchase_order,
        DocType.BANK_STATEMENT: parse_bank_statement,
        DocType.EXPENSE_REPORT: parse_expense_report,
        DocType.CREDIT_NOTE: parse_credit_debit_note,
        DocType.DEBIT_NOTE: parse_credit_debit_note,
    }
    
    parsed_count = 0
    for doc in documents:
        parser = parsers.get(doc.doc_type)
        if parser:
            try:
                doc.parsed = parser(doc)
                parsed_count += 1
            except Exception as e:
                print(f"  ⚠️ Error parsing {doc.doc_id}: {e}")
    
    print(f"✅ Parsed {parsed_count}/{len(documents)} documents")
    return documents
