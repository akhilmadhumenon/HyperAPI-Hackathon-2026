"""
Easy needle detectors (Tier 1 — 40 needles × 1 pt = 40 pts).
Single-document checks: arithmetic, dates, duplicate line items, tax rates.
"""
import re
import calendar
from typing import List
from src.models import Finding, Document, DocType, Invoice, LineItem
from src.tax_rates import get_expected_tax_rate


def detect_arithmetic_errors(documents: List[Document]) -> List[Finding]:
    """
    Check each invoice for:
    - qty × rate ≠ amount (per line)
    - sum(line amounts) ≠ subtotal
    - tax calculation errors
    - subtotal + tax ≠ grand total
    """
    findings = []
    
    for doc in documents:
        if doc.doc_type != DocType.INVOICE or not doc.parsed:
            continue
        
        inv: Invoice = doc.parsed
        
        # Check each line item: qty × rate = amount
        for i, item in enumerate(inv.line_items):
            if item.qty > 0 and item.rate > 0 and item.amount > 0:
                expected = round(item.qty * item.rate, 2)
                # Combination of absolute and relative check
                rel_diff = abs(expected - item.amount) / item.amount if item.amount != 0 else 0
                if abs(expected - item.amount) > 0.1 and rel_diff > 0.001: 
                    findings.append(Finding(
                        category="arithmetic_error",
                        pages=inv.pages,
                        document_refs=[inv.doc_id],
                        description=f"Line item {i+1}: qty({item.qty}) × rate({item.rate}) = {expected}, but amount shown as {item.amount}",
                        reported_value=str(item.amount),
                        correct_value=str(expected),
                    ))
        
        # Check subtotal = sum of line amounts
        # DROPPED sum < subtotal check because it's usually an extraction miss
        if len(inv.line_items) >= 2 and inv.subtotal > 0:
            line_sum = round(sum(item.amount for item in inv.line_items), 2)
            # Only report if Sum > Subtotal (very rare unless real error)
            if line_sum > inv.subtotal + 5.00:
                findings.append(Finding(
                    category="arithmetic_error",
                    pages=inv.pages,
                    document_refs=[inv.doc_id],
                    description=f"Sum of line items ({line_sum}) exceeds subtotal ({inv.subtotal})",
                    reported_value=str(inv.subtotal),
                    correct_value=str(line_sum),
                ))
        
        # Check grand total = subtotal + tax
        # This is the most reliable arithmetic error
        if inv.subtotal > 0 and inv.tax_total > 0 and inv.grand_total > 0:
            expected_gt = round(inv.subtotal + inv.tax_total, 2)
            if abs(expected_gt - inv.grand_total) > 0.50:
                findings.append(Finding(
                    category="arithmetic_error",
                    pages=inv.pages,
                    document_refs=[inv.doc_id],
                    description=f"Arithmetic error: Subtotal ({inv.subtotal}) + Tax ({inv.tax_total}) = {expected_gt}, but grand total shows {inv.grand_total}",
                    reported_value=str(inv.grand_total),
                    correct_value=str(expected_gt),
                ))
    
    return findings


def detect_billing_typo(documents: List[Document]) -> List[Finding]:
    """
    Detect hours logged as decimal (e.g. 1.30) when it should be decimal hours (1.50).
    Common billing typo: XX.15 -> XX.25, XX.20 -> XX.33, XX.30 -> XX.50, XX.45 -> XX.75
    """
    findings = []
    
    # Map from 'minute' part (after decimal) to correct decimal fraction
    MINUTE_MAP = {
        15: 0.25,
        20: 0.33,
        30: 0.50,
        40: 0.67,
        45: 0.75,
        50: 0.83
    }
    
    for doc in documents:
        if doc.doc_type != DocType.INVOICE or not doc.parsed:
            continue
        
        inv: Invoice = doc.parsed
        
        for i, item in enumerate(inv.line_items):
            qty = item.qty
            if qty <= 0:
                continue
            
            # Extract decimal part as integer minutes
            frac = round(qty - int(qty), 2)
            mins = int(round(frac * 100))
            
            # Stricter conditions for a billing typo:
            # 1. Decimal part matches a common, suspicious minute count (15, 30, 45, 40)
            # 2. Unit is strictly time-based ('Hrs', 'Hours')
            # 3. The reported amount confirms the typo was used in calculation
            
            unit = (item.unit or "").lower()
            desc = (item.description or "").lower()
            
            # Use strict unit check
            is_hour_unit = any(u == unit for u in ["hrs", "hr", "hours", "hour"])
            
            # Exclude 20, 50 as they are common valid decimals (1/5, 1/2) unless unit is explicit
            if mins in [15, 30, 45, 40] and is_hour_unit:
                correct_frac = MINUTE_MAP.get(mins)
                if not correct_frac: continue
                
                correct_qty = int(qty) + correct_frac
                
                reported_amt = item.amount
                calc_amt_typo = round(qty * item.rate, 2)
                
                # Verify financial impact
                if abs(reported_amt - calc_amt_typo) < 0.10:
                    correct_amt = round(correct_qty * item.rate, 2)
                    
                    if abs(correct_amt - reported_amt) > 0.10:
                        findings.append(Finding(
                            category="billing_typo",
                            pages=inv.pages,
                            document_refs=[inv.doc_id],
                            description=(
                                f"Line item {i+1}: Billing typo in hours. "
                                f"Logged '{qty}' {item.unit}, likely meant '{correct_qty}' decimal hours "
                                f"({mins} minutes = {correct_frac} hours)."
                            ),
                            reported_value=f"{reported_amt:.2f}",
                            correct_value=f"{correct_amt:.2f}",
                            confidence=0.9
                        ))
    
    return findings


def detect_duplicate_line_items(documents: List[Document]) -> List[Finding]:
    """
    Detect exact same line item appearing twice in one invoice.
    """
    findings = []
    
    for doc in documents:
        if doc.doc_type != DocType.INVOICE or not doc.parsed:
            continue
        
        inv: Invoice = doc.parsed
        seen = {}
        
        for i, item in enumerate(inv.line_items):
            key = (item.description.lower().strip(), item.qty, item.rate, item.amount)
            if key in seen:
                findings.append(Finding(
                    category="duplicate_line_item",
                    pages=inv.pages,
                    document_refs=[inv.doc_id],
                    description=f"Line item '{item.description}' appears at positions {seen[key]+1} and {i+1} with identical qty/rate/amount ({item.qty}/{item.rate}/{item.amount})",
                    reported_value=str(item.amount * 2),
                    correct_value=str(item.amount),
                ))
            else:
                seen[key] = i
    
    return findings


def detect_invalid_dates(documents: List[Document]) -> List[Finding]:
    """
    Detect impossible calendar dates only in the structured fields.
    - Feb 29 in non-leap year
    - Feb 30, Feb 31, etc.
    """
    findings = []
    
    MONTH_NAMES = {
        'january': 1, 'february': 2, 'march': 3, 'april': 4,
        'may': 5, 'june': 6, 'july': 7, 'august': 8,
        'september': 9, 'october': 10, 'november': 11, 'december': 12
    }
    
    for doc in documents:
        if not doc.parsed:
            continue
        
        # Collect all dates extracted from this document
        dates_to_check = []
        if hasattr(doc.parsed, 'date') and doc.parsed.date:
            dates_to_check.append(doc.parsed.date)
        
        if hasattr(doc.parsed, 'transactions'):
            for txn in doc.parsed.transactions:
                if txn.date:
                    dates_to_check.append(txn.date)
        
        if hasattr(doc.parsed, 'line_items'):
            for item in doc.parsed.line_items:
                if hasattr(item, 'date') and item.date:
                    dates_to_check.append(item.date)
        
        for date_str in set(dates_to_check):
            # Parse DD/MM/YYYY or DD-MM-YYYY
            match = re.search(r'(\d{1,2})[/-](\d{1,2})[/-](\d{4})', date_str)
            if match:
                day, month, year = int(match.group(1)), int(match.group(2)), int(match.group(3))
                invalid_reason = check_date_validity(day, month, year)
                if invalid_reason:
                    findings.append(Finding(
                        category="invalid_date",
                        pages=doc.pages,
                        document_refs=[doc.doc_id],
                        description=f"Invalid date {date_str}: {invalid_reason}",
                        reported_value=date_str,
                        correct_value="",
                    ))
            else:
                # Try DD Month YYYY
                match = re.search(r'(\d{1,2})\s+([A-Za-z]+)\s+(\d{4})', date_str)
                if match:
                    day = int(match.group(1))
                    m_name = match.group(2).lower()
                    month = MONTH_NAMES.get(m_name, 0)
                    year = int(match.group(3))
                    if month > 0:
                        invalid_reason = check_date_validity(day, month, year)
                        if invalid_reason:
                            findings.append(Finding(
                                category="invalid_date",
                                pages=doc.pages,
                                document_refs=[doc.doc_id],
                                description=f"Invalid date {date_str}: {invalid_reason}",
                                reported_value=date_str,
                                correct_value="",
                            ))
    
    return findings


def check_date_validity(day: int, month: int, year: int) -> str:
    """Check if a date is valid. Returns error description or empty string."""
    if month < 1 or month > 12:
        return f"Month {month} is out of range (1-12)"
    
    if day < 1:
        return f"Day {day} is invalid (must be >= 1)"
    
    if day > 31:
        return f"Day {day} exceeds maximum (31)"
    
    # Check month-specific limits
    max_days = {
        1: 31, 2: 29, 3: 31, 4: 30, 5: 31, 6: 30,
        7: 31, 8: 31, 9: 30, 10: 31, 11: 30, 12: 31
    }
    
    if day > max_days.get(month, 31):
        month_name = calendar.month_name[month]
        return f"{month_name} has only {max_days[month]} days, not {day}"
    
    # Leap year check for Feb 29
    if month == 2 and day == 29:
        if not calendar.isleap(year):
            return f"Feb 29 in {year} is invalid — {year} is not a leap year"
    
    return ""


def detect_wrong_tax_rate(documents: List[Document]) -> List[Finding]:
    """
    Check if tax rates match standard GST slabs (5%, 12%, 18%, 28%).
    
    A 'wrong_tax_rate' needle is when a vendor applied a VALID GST slab
    (e.g. 12%) instead of the correct one (e.g. 18%) for the given HSN code.
    
    We detect this by checking if tax_total/subtotal precisely matches one of
    the standard slab multipliers. If it does match a slab but NOT the expected
    one for the HSN → wrong_tax_rate. If tax doesn't match ANY slab precisely →
    arithmetic_error (the math is just wrong).
    """
    findings = []
    VALID_SLABS = [5.0, 12.0, 18.0, 28.0]
    
    for doc in documents:
        if doc.doc_type != DocType.INVOICE or not doc.parsed:
            continue
        
        inv: Invoice = doc.parsed
        if not inv.subtotal or inv.subtotal <= 0 or not inv.tax_total:
            continue
        
        # Determine expected rate from HSN codes
        # Strategy: Use the dominant rate (by value). Tolerates unknowns up to 60%.
        rate_values = {}  # rate -> total amount
        total_known_value = 0.0
        total_value = 0.0
        
        for item in inv.line_items:
            hsn = ''.join(filter(str.isalnum, item.hsn_sac or ''))
            r = get_expected_tax_rate(hsn)
            total_value += item.amount
            if r >= 0: # 0 is a valid rate (exempt)
                rate_values[r] = rate_values.get(r, 0) + item.amount
                total_known_value += item.amount
        
        if not rate_values or total_value <= 0:
            continue
            
        # Relaxed threshold for unknowns: up to 60%
        if (total_value - total_known_value) > total_value * 0.60:
            continue
            
        # Find dominant rate
        expected = max(rate_values, key=rate_values.get)
        
        # Strategy: If they applied a standard slab (12, 18, 28) blanket-wise, 
        # and that slab isn't even one of the HSN rates, or differs from the dominant one, it's a needle.
        # Dominant rate must cover at least 45% of known value (handles 50/50 splits)
        if rate_values[expected] < total_known_value * 0.45:
            continue
        
        # Compute the actual tax multiplier precisely
        multiplier = inv.tax_total / inv.subtotal
        
        # Identify which slab was actually applied (if any)
        # Use tight tolerance: 0.5% on the rate = 0.005 on multiplier
        applied_slab = None
        for slab in VALID_SLABS:
            if abs(multiplier - (slab / 100.0)) < 0.005:
                applied_slab = slab
                break
        
        if applied_slab is not None and applied_slab != expected:
            # They precisely applied a WRONG but valid GST slab
            # Find a representative HSN to display
            hsn_display = None
            for item in inv.line_items:
                hsn = ''.join(filter(str.isalnum, item.hsn_sac or ''))
                if get_expected_tax_rate(hsn) == expected:
                    hsn_display = item.hsn_sac
                    break
            hsn_display = hsn_display or inv.line_items[0].hsn_sac
            
            findings.append(Finding(
                category="wrong_tax_rate",
                pages=inv.pages,
                document_refs=[inv.doc_id],
                description=(
                    f"Invoice applied {applied_slab}% GST rate, "
                    f"but HSN {hsn_display} requires {expected}%"
                ),
                reported_value=f"{applied_slab}%",
                correct_value=f"{expected}%",
            ))
    
    return findings


def run_easy_detectors(documents: List[Document]) -> List[Finding]:
    """Run all easy-tier detectors."""
    print("\n🟢 Running EASY detectors (Tier 1)...")
    
    all_findings = []
    
    detectors = [
        ("arithmetic_error", detect_arithmetic_errors),
        ("billing_typo", detect_billing_typo),
        ("duplicate_line_item", detect_duplicate_line_items),
        ("invalid_date", detect_invalid_dates),
        ("wrong_tax_rate", detect_wrong_tax_rate),
    ]
    
    for name, detector in detectors:
        findings = detector(documents)
        print(f"  {name}: {len(findings)} findings")
        all_findings.extend(findings)
    
    print(f"  Total easy findings: {len(all_findings)}")
    return all_findings
