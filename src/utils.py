import re
from typing import Optional, Tuple

_MONTH_NAMES = {
    'jan': 1, 'feb': 2, 'mar': 3, 'apr': 4, 'may': 5, 'jun': 6,
    'jul': 7, 'aug': 8, 'sep': 9, 'oct': 10, 'nov': 11, 'dec': 12,
    'january': 1, 'february': 2, 'march': 3, 'april': 4,
    'june': 6, 'july': 7, 'august': 8, 'september': 9,
    'october': 10, 'november': 11, 'december': 12,
}

STATE_ALIASES = {
    "up": "Uttar Pradesh", "mp": "Madhya Pradesh", "ap": "Andhra Pradesh", "mh": "Maharashtra",
    "tn": "Tamil Nadu", "ka": "Karnataka", "wb": "West Bengal", "dl": "Delhi", "hr": "Haryana",
    "pb": "Punjab", "rj": "Rajasthan", "guj": "Gujarat", "tel": "Telangana", "ker": "Kerala",
    "jk": "Jammu & Kashmir", "uk": "Uttarakhand", "ts": "Telangana", "as": "Assam",
    "py": "Puducherry", "ga": "Goa", "br": "Bihar", "jh": "Jharkhand", "or": "Odisha",
    "ct": "Chhattisgarh", "sk": "Sikkim", "ml": "Meghalaya", "tr": "Tripura", "mz": "Mizoram",
    "nl": "Nagaland", "mn": "Manipur", "ar": "Arunachal Pradesh", "hp": "Himachal Pradesh",
}

def normalize_state(s: str) -> str:
    """Normalize Indian state names for comparison."""
    if not s: return ""
    s = s.lower().strip().replace(".", "")
    if s in STATE_ALIASES:
        return STATE_ALIASES[s].lower()
    # Handle specific cases like "Andhra Pradesh (New)"
    s = s.replace("(new)", "").strip()
    return s

def parse_date_to_tuple(date_str: str) -> Optional[Tuple[int, int, int]]:
    """Parse a date string into a (year, month, day) tuple."""
    if not date_str:
        return None

    # DD/MM/YYYY or DD-MM-YYYY
    m = re.search(r'(\d{1,2})[/-](\d{1,2})[/-](\d{4})', date_str)
    if m:
        d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if mo > 12: d, mo = mo, d # handle MM/DD swapping
        return (y, mo, d)

    # DD Month YYYY
    m = re.search(r'(\d{1,2})\s+([A-Za-z]+)\s+(\d{4})', date_str)
    if m:
        d = int(m.group(1))
        mo = _MONTH_NAMES.get(m.group(2).lower(), 0)
        y = int(m.group(3))
        if mo > 0:
            return (y, mo, d)

    return None
