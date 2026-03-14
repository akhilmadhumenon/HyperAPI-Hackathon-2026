"""
Deterministic PDF segmentation using pypdf text extraction.
Splits gauntlet.pdf into logical document segments based on page headers.
Outputs segments.json: list of {doc_type, page_start, page_end, doc_ref}.
"""
import json
import os
import re
import pypdf

PDF_PATH = os.path.join(os.path.dirname(__file__), 'gauntlet.pdf')
SEGMENTS_JSON = os.path.join(os.path.dirname(__file__), 'segments.json')

# Document type headers (must appear as the FIRST non-empty line of a page)
DOC_HEADERS = {
    'TAX INVOICE': 'TAX INVOICE',
    'PURCHASE ORDER': 'PURCHASE ORDER',
    'BANK STATEMENT': 'BANK STATEMENT',
    'EXPENSE REPORT': 'EXPENSE REPORT',
    'CREDIT NOTE': 'CREDIT NOTE',
    'DEBIT NOTE': 'DEBIT NOTE',
    'QUOTATION': 'QUOTATION',
    'DELIVERY NOTE': 'DELIVERY NOTE',
    'REMITTANCE ADVICE': 'REMITTANCE ADVICE',
    'PAYMENT RECEIPT': 'PAYMENT RECEIPT',
    'VENDOR MASTER': 'VENDOR MASTER',
    'MEETING MINUTES': 'MEETING MINUTES',
    'TERMS AND CONDITIONS': 'TERMS AND CONDITIONS',
    'PROJECT STATUS UPDATE': 'PROJECT STATUS UPDATE',
    'AUDIT TRAIL REPORT': 'AUDIT TRAIL REPORT',
    'COMPLIANCE CHECKLIST': 'COMPLIANCE CHECKLIST',
    'VENDOR EVALUATION FORM': 'VENDOR EVALUATION FORM',
    'INTERNAL MEMO': 'INTERNAL MEMO',
    'BUDGET REVIEW SUMMARY': 'BUDGET REVIEW SUMMARY',
}

# Continuation markers — these pages belong to the preceding document
CONTINUATION_MARKERS = [
    '(continued)', '(cont.)', '- continued', '- page 2',
    '(continued - page', '(page 2', '(page 3',
]


def is_continuation(first_line: str) -> bool:
    fl = first_line.lower().strip()
    for marker in CONTINUATION_MARKERS:
        if marker in fl:
            return True
    # e.g. "TAX INVOICE (Continued)"
    if re.search(r'\(continued\)', fl, re.IGNORECASE):
        return True
    return False


def classify_page(text: str) -> str | None:
    """Return doc_type if this page starts a new document, else None."""
    lines = [l.strip() for l in text.split('\n') if l.strip()]
    if not lines:
        return None
    first = lines[0]
    if is_continuation(first):
        return None
    for header in DOC_HEADERS:
        if first.upper().startswith(header):
            return DOC_HEADERS[header]
    return None


def extract_doc_ref(text: str, doc_type: str) -> str | None:
    """Extract the primary document reference (invoice no, PO no, etc.) from page text."""
    lines = text.split('\n')

    def after_label(label: str) -> str | None:
        for i, line in enumerate(lines):
            if re.match(r'\s*' + re.escape(label) + r'\s*:?\s*$', line.strip(), re.IGNORECASE):
                if i + 1 < len(lines):
                    val = lines[i + 1].strip()
                    if val:
                        return val
        return None

    if doc_type == 'TAX INVOICE':
        return after_label('Invoice No') or after_label('Invoice Number')
    if doc_type == 'PURCHASE ORDER':
        return after_label('PO Number') or after_label('PO No')
    if doc_type == 'BANK STATEMENT':
        return after_label('Statement ID') or after_label('Statement No')
    if doc_type == 'EXPENSE REPORT':
        return after_label('Report ID') or after_label('Report No')
    if doc_type == 'CREDIT NOTE':
        return after_label('CREDIT NOTE No') or after_label('Credit Note No')
    if doc_type == 'DEBIT NOTE':
        return after_label('DEBIT NOTE No') or after_label('Debit Note No')
    if doc_type == 'DELIVERY NOTE':
        return after_label('DN NO') or after_label('DN No') or after_label('Delivery Note No')
    if doc_type == 'REMITTANCE ADVICE':
        return after_label('Advice No') or after_label('Advice Number') or after_label('RA No')
    if doc_type == 'TERMS AND CONDITIONS':
        return after_label('Document') or after_label('Document ID') or after_label('Document No')
    if doc_type in ('BUDGET REVIEW SUMMARY', 'COMPLIANCE CHECKLIST',
                    'VENDOR EVALUATION FORM', 'AUDIT TRAIL REPORT',
                    'MEETING MINUTES', 'INTERNAL MEMO',
                    'PROJECT STATUS UPDATE'):
        return (after_label('Document ID') or after_label('Document No')
                or after_label('Report ID') or after_label('Memo ID'))
    if doc_type == 'VENDOR MASTER':
        return 'VENDOR-MASTER'
    return None


def split_pdf(pdf_path: str = PDF_PATH) -> list[dict]:
    """
    Iterate all pages and group them into document segments.
    Returns list of {doc_type, doc_ref, page_start, page_end} (1-indexed pages).
    """
    reader = pypdf.PdfReader(pdf_path)
    total = len(reader.pages)
    print(f"[custom_split] Total pages: {total}")

    segments = []
    current_type = None
    current_ref = None
    current_start = None

    for i in range(total):
        text = reader.pages[i].extract_text() or ''
        dtype = classify_page(text)

        if dtype is not None:
            # Save the previous segment
            if current_type is not None:
                segments.append({
                    'doc_type': current_type,
                    'doc_ref': current_ref,
                    'page_start': current_start,
                    'page_end': i,  # exclusive (1-indexed end = i, so pages current_start..i)
                })
            current_type = dtype
            current_ref = extract_doc_ref(text, dtype)
            current_start = i + 1  # 1-indexed

    # Flush last segment
    if current_type is not None:
        segments.append({
            'doc_type': current_type,
            'doc_ref': current_ref,
            'page_start': current_start,
            'page_end': total,
        })

    print(f"[custom_split] Found {len(segments)} document segments")
    return segments


def save_segments(segments: list[dict], out_path: str = SEGMENTS_JSON):
    with open(out_path, 'w') as f:
        json.dump(segments, f, indent=2)
    print(f"[custom_split] Saved segments to {out_path}")


def load_segments(path: str = SEGMENTS_JSON) -> list[dict]:
    with open(path) as f:
        return json.load(f)


if __name__ == '__main__':
    segs = split_pdf()
    save_segments(segs)
    # Print summary
    from collections import Counter
    counts = Counter(s['doc_type'] for s in segs)
    for t, c in counts.most_common():
        print(f"  {c:4d}  {t}")
