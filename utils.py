"""Utility helpers for the Financial Gauntlet pipeline."""
import re
from datetime import datetime, date
from difflib import SequenceMatcher

# ── Amount parsing ────────────────────────────────────────────────────────────

def parse_amount(s: str) -> float | None:
    """Strip ■/₹/,/spaces and return float. Returns None on failure."""
    if s is None:
        return None
    s = str(s).replace('■', '').replace('₹', '').replace(',', '').replace(' ', '').strip()
    s = s.lstrip('-')  # handle negative display
    try:
        return float(s)
    except ValueError:
        return None


def amounts_equal(a: float | None, b: float | None, tol: float = 0.02) -> bool:
    """True if both are non-None and within absolute tolerance (default ₹0.02)."""
    if a is None or b is None:
        return False
    return abs(a - b) <= tol


def pct_diff(a: float, b: float) -> float:
    """Percentage difference (absolute) between a and b."""
    if b == 0:
        return 0.0
    return abs(a - b) / abs(b) * 100


# ── Date parsing ──────────────────────────────────────────────────────────────

def parse_date(s: str) -> str | None:
    """
    Parse DD/MM/YYYY → 'YYYY-MM-DD'. Returns None on failure.
    Does NOT validate calendar correctness (use validate_date for that).
    """
    if not s:
        return None
    s = s.strip()
    m = re.match(r'(\d{1,2})/(\d{1,2})/(\d{4})', s)
    if m:
        d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        return f"{y:04d}-{mo:02d}-{d:02d}"
    # try YYYY-MM-DD passthrough
    m2 = re.match(r'(\d{4})-(\d{2})-(\d{2})', s)
    if m2:
        return s[:10]
    return None


def validate_date(date_str: str) -> tuple[bool, str]:
    """
    Check if a 'YYYY-MM-DD' string is a real calendar date.
    Returns (is_valid, reason). Reason is empty string if valid.
    """
    if not date_str:
        return False, "empty date"
    try:
        y, m, d = map(int, date_str.split('-'))
    except Exception:
        return False, f"unparseable: {date_str}"

    if d == 0:
        return False, f"day 00 in {date_str}"
    if m == 0 or m > 12:
        return False, f"invalid month {m} in {date_str}"

    # days per month (without leap check)
    max_days = [0, 31, 29, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
    if d > max_days[m]:
        return False, f"day {d} impossible for month {m} in {date_str}"

    # Feb 29 only valid in leap year
    if m == 2 and d == 29:
        if not (y % 4 == 0 and (y % 100 != 0 or y % 400 == 0)):
            return False, f"Feb 29 in non-leap year {y}"

    # Months with max 30 days
    if m in (4, 6, 9, 11) and d > 30:
        return False, f"day {d} impossible for month {m} in {date_str}"

    return True, ""


# ── GSTIN helpers ─────────────────────────────────────────────────────────────

STATE_CODES = {
    "01": "Jammu and Kashmir", "02": "Himachal Pradesh", "03": "Punjab",
    "04": "Chandigarh", "05": "Uttarakhand", "06": "Haryana",
    "07": "Delhi", "08": "Rajasthan", "09": "Uttar Pradesh",
    "10": "Bihar", "11": "Sikkim", "12": "Arunachal Pradesh",
    "13": "Nagaland", "14": "Manipur", "15": "Mizoram",
    "16": "Tripura", "17": "Meghalaya", "18": "Assam",
    "19": "West Bengal", "20": "Jharkhand", "21": "Odisha",
    "22": "Chhattisgarh", "23": "Madhya Pradesh", "24": "Gujarat",
    "25": "Daman and Diu", "26": "Dadra and Nagar Haveli",
    "27": "Maharashtra", "28": "Andhra Pradesh (old)",
    "29": "Karnataka", "30": "Goa", "31": "Lakshadweep",
    "32": "Kerala", "33": "Tamil Nadu", "34": "Puducherry",
    "35": "Andaman and Nicobar Islands", "36": "Telangana",
    "37": "Andhra Pradesh", "38": "Ladakh",
    "96": "Foreign", "97": "Other Territory", "99": "Centre",
}

# Alias map: state name variations → canonical code
STATE_NAME_TO_CODE = {v.lower(): k for k, v in STATE_CODES.items()}
STATE_NAME_TO_CODE.update({
    "andhra pradesh": "37",
    "j&k": "01",
    "jammu & kashmir": "01",
    "uttaranchal": "05",
})


def gstin_state_code(gstin: str) -> str | None:
    """Return the 2-digit state prefix from a GSTIN string."""
    if gstin and len(gstin) >= 2:
        return gstin[:2]
    return None


def state_name_to_code(state: str) -> str | None:
    """Map a state name (from Vendor Master) to its 2-digit GST code."""
    if not state:
        return None
    return STATE_NAME_TO_CODE.get(state.strip().lower())


# ── HSN / SAC → GST rate mapping ─────────────────────────────────────────────
# Source: common Indian GST tariff rates.
# Format: prefix → rate (%)  (match by startswith, most specific first)

HSN_GST_RATES = [
    # SAC codes (6-digit services) — check most specific first
    ("9965", 5),   # Goods transport by road (GTA)
    ("9966", 5),   # Transport of passengers (bus/rail/ferry excl. air)
    ("9992", 18),  # Postal / courier services
    ("9973", 18),  # Financial/insurance services
    ("9971", 18),  # Financial intermediation
    ("9968", 18),  # Postal services
    ("9961", 18),  # Retail trade services
    ("9954", 18),  # Construction services
    ("9986", 12),  # Support services to agriculture
    ("9987", 18),  # Maintenance/repair
    ("9981", 18),  # Legal services
    ("9982", 18),  # Accounting/audit/taxation
    ("9983", 18),  # Technical/business/management consulting
    ("9984", 18),  # Telecom/IT services
    ("9985", 18),  # Support/business services
    ("9988", 5),   # Manufacturing services on goods owned by others (job work)
    ("9989", 18),  # Other manufacturing services
    ("999", 18),   # Catch-all services
    # HSN goods codes
    ("01", 0),     # Live animals
    ("02", 0),     # Meat
    ("03", 5),     # Fish
    ("04", 5),     # Dairy
    ("07", 5),     # Vegetables
    ("08", 12),    # Fruits
    ("09", 5),     # Spices
    ("10", 0),     # Cereals
    ("11", 5),     # Milling products
    ("17", 5),     # Sugar
    ("19", 18),    # Preparations of cereals
    ("22", 18),    # Beverages
    ("24", 28),    # Tobacco
    ("27", 5),     # Mineral fuels
    ("28", 18),    # Inorganic chemicals
    ("29", 18),    # Organic chemicals
    ("30", 12),    # Pharmaceuticals
    ("34", 18),    # Soaps/detergents
    ("39", 18),    # Plastics
    ("40", 18),    # Rubber
    ("44", 18),    # Wood
    ("47", 12),    # Pulp/paper
    ("48", 12),    # Paper/paperboard articles
    ("49", 12),    # Printed books/newspapers
    ("50", 5),     # Silk
    ("52", 5),     # Cotton
    ("61", 5),     # Knitted clothing
    ("62", 5),     # Woven clothing
    ("63", 5),     # Other textiles
    ("64", 18),    # Footwear
    ("73", 18),    # Iron/steel articles
    ("74", 18),    # Copper articles
    ("76", 18),    # Aluminium articles
    ("82", 18),    # Tools
    ("83", 18),    # Misc metal articles
    ("84", 18),    # Machinery/mechanical appliances
    ("85", 18),    # Electrical equipment
    ("87", 28),    # Vehicles
    ("90", 18),    # Optical/photo equipment
    ("94", 18),    # Furniture
    ("95", 18),    # Toys/games
    ("96", 18),    # Misc manufactured articles
]


def expected_gst_rate(hsn: str) -> int | None:
    """Return expected GST % for an HSN/SAC code, or None if unknown."""
    if not hsn:
        return None
    h = str(hsn).strip()
    for prefix, rate in HSN_GST_RATES:
        if h.startswith(prefix):
            return rate
    return None


# ── Vendor name fuzzy matching ────────────────────────────────────────────────

def similarity(a: str, b: str) -> float:
    """SequenceMatcher ratio between two strings (case-insensitive)."""
    return SequenceMatcher(None, a.lower().strip(), b.lower().strip()).ratio()


def find_vendor_match(name: str, vendor_list: list[dict], threshold: float = 0.85) -> dict | None:
    """
    Find the best matching vendor from vendor_list by name.
    Returns vendor dict if best match >= threshold, else None.
    vendor_list items: dicts with 'name' key.
    """
    if not name or not vendor_list:
        return None
    best_score = 0.0
    best_vendor = None
    name_lower = name.lower().strip()
    for v in vendor_list:
        vname = v.get('name', '')
        score = similarity(name_lower, vname.lower().strip())
        # Also try prefix match for truncated names
        short = min(len(name_lower), len(vname.lower().strip()))
        if short > 10:
            prefix_score = similarity(name_lower[:short], vname.lower().strip()[:short])
            score = max(score, prefix_score)
        if score > best_score:
            best_score = score
            best_vendor = v
    if best_score >= threshold:
        return best_vendor
    return None


def is_vendor_name_typo(invoice_name: str, master_name: str) -> bool:
    """
    Returns True if names are similar enough to be the same vendor but not identical.
    Threshold: >= 0.7 similarity but not exact.
    """
    inv = invoice_name.lower().strip()
    mst = master_name.lower().strip()
    if inv == mst:
        return False
    return similarity(inv, mst) >= 0.70


# ── Text field extraction ─────────────────────────────────────────────────────

def extract_field_after_label(text: str, label: str) -> str | None:
    """
    In pypdf-extracted text, fields appear as:
        Label:
        value
    Returns the value on the line immediately after 'label:' (case-insensitive).
    """
    lines = text.split('\n')
    for i, line in enumerate(lines):
        if re.search(re.escape(label) + r'\s*:?\s*$', line.strip(), re.IGNORECASE):
            if i + 1 < len(lines):
                val = lines[i + 1].strip()
                if val:
                    return val
    return None


def extract_inline_field(text: str, label: str) -> str | None:
    """
    Try to extract a value on the same line as a label:
        e.g. 'Invoice No: INV-2025-00042'
    Returns the part after the colon.
    """
    m = re.search(re.escape(label) + r'\s*:\s*(.+)', text, re.IGNORECASE)
    if m:
        return m.group(1).strip()
    return None


def extract_amount_before_label(text: str, label: str) -> float | None:
    """
    In invoice text, total values appear BEFORE their labels:
        ■9,00,279.52
        Subtotal:
    Returns the numeric value from the line immediately before 'label'.
    """
    lines = text.split('\n')
    for i, line in enumerate(lines):
        if re.match(r'\s*' + re.escape(label) + r'\s*:?\s*$', line.strip(), re.IGNORECASE):
            if i > 0:
                val = lines[i - 1].strip()
                return parse_amount(val)
    return None
