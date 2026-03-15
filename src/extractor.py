"""
PDF text extraction using pdfplumber (fast, local, free).
Enhanced document boundary detection and classification.
"""
import pdfplumber
import re
from typing import List, Dict, Tuple, Optional
from tqdm import tqdm

from .models import PageData, Document, DocType


def extract_all_pages(pdf_path: str, max_pages: int = None) -> List[PageData]:
    """Extract text and tables from every page of the PDF."""
    pages = []
    
    with pdfplumber.open(pdf_path) as pdf:
        total = min(len(pdf.pages), max_pages) if max_pages else len(pdf.pages)
        print(f"📄 Extracting text from {total} pages...")
        
        # Try to detect offset from the first page if possible
        offset = 0
        if total > 0:
            first_text = pdf.pages[0].extract_text() or ""
            lines = first_text.strip().split('\n')
            if lines:
                last_line = lines[-1]
                match = re.search(r'Page\s+(\d+)', last_line, re.IGNORECASE)
                if match:
                    # If Physical Page 1 is Printed Page 5, offset is 5 - 1 = 4
                    offset = int(match.group(1)) - 1
        
        for i in tqdm(range(total), desc="Extracting pages"):
            page = pdf.pages[i]
            text = page.extract_text() or ""
            
            # 1. Try robust bottom-left detection first
            extracted_num = None
            try:
                # Crop bottom 10% and left 50% (allowing some center variance)
                footer_bbox = (0, page.height * 0.9, page.width * 0.5, page.height)
                footer_text = page.within_bbox(footer_bbox).extract_text() or ""
                match = re.search(r'Page\s+(\d+)', footer_text, re.IGNORECASE)
                if match:
                    extracted_num = int(match.group(1))
            except Exception:
                pass

            # 2. Fallback to standard bottom-of-text if bbox failed
            if extracted_num is None:
                lines = text.strip().split('\n')
                if lines:
                    last_line = lines[-1]
                    match = re.search(r'Page\s+(\d+)', last_line, re.IGNORECASE)
                    if match:
                        try:
                            extracted_num = int(match.group(1))
                        except (ValueError, TypeError):
                            pass

            # 3. Apply sanity check and use offset if still nothing
            page_num = i + 1 + offset
            if extracted_num is not None:
                # Basic sanity check: extracted number shouldn't be crazy far from expected
                if abs(extracted_num - page_num) < 5:
                    page_num = extracted_num
            
            tables = []
            try:
                page_tables = page.extract_tables()
                if page_tables:
                    tables = page_tables
            except Exception:
                pass
            
            pages.append(PageData(
                page_num=page_num,
                text=text,
                tables=tables
            ))
    
    print(f"✅ Extracted {len(pages)} pages")
    return pages


# Document header patterns — check only the FIRST few lines of a page
DOC_HEADERS = {
    DocType.INVOICE: r'^TAX\s+INVOICE',
    DocType.PURCHASE_ORDER: r'^PURCHASE\s+ORDER',
    DocType.BANK_STATEMENT: r'^BANK\s+STATEMENT',
    DocType.EXPENSE_REPORT: r'^EXPENSE\s+REPORT',
    DocType.CREDIT_NOTE: r'^CREDIT\s+NOTE',
    DocType.DEBIT_NOTE: r'^DEBIT\s+NOTE',
    DocType.RECEIPT: r'^RECEIPT\b',
    DocType.QUOTATION: r'^QUOTATION\b',
    DocType.DELIVERY_NOTE: r'^DELIVERY\s+(?:NOTE|CHALLAN)',
}

# Document ID patterns
DOC_ID_PATTERNS = {
    'INV': (DocType.INVOICE, r'(INV-\d{4}-\d+)'),
    'PO': (DocType.PURCHASE_ORDER, r'(PO-\d{4}-\d+)'),
    'BS': (DocType.BANK_STATEMENT, r'(BS-\d{4}-\d+)'),
    'EXP': (DocType.EXPENSE_REPORT, r'(EXP-\d{4}-\d+)'),
    'ER': (DocType.EXPENSE_REPORT, r'(ER-\d{4}-\d+)'),
    'CN': (DocType.CREDIT_NOTE, r'(CN-\d{4}-\d+)'),
    'DN': (DocType.DEBIT_NOTE, r'(DN-\d{4}-\d+)'),
    'REC': (DocType.RECEIPT, r'(REC-\d{4}-\d+)'),
    'QUO': (DocType.QUOTATION, r'(QUO-\d{4}-\d+)'),
    'DEL': (DocType.DELIVERY_NOTE, r'(DEL-\d{4}-\d+)'),
}

ALL_DOC_ID_RE = re.compile(
    r'((?:INV|PO|BS|EXP|ER|CN|DN|REC|QUO|DEL)-\d{4}-\d+)'
)


def classify_page(text: str) -> Tuple[Optional[DocType], Optional[str]]:
    """
    Classify a page by detecting its header and document ID.
    Returns (doc_type, doc_id) or (None, None) for continuation pages.
    """
    lines = text.strip().split('\n')
    if not lines:
        return None, None
    
    # Check the first 3 lines for document header
    header_text = '\n'.join(lines[:3]).strip()
    
    # NEW: Skip classification if it's a continuation page
    if "(Continued)" in header_text:
        return None, None
    
    for doc_type, pattern in DOC_HEADERS.items():
        if re.search(pattern, header_text, re.IGNORECASE | re.MULTILINE):
            # Found a document header — now get the ID
            doc_id = None
            # Search first 10 lines for document ID
            search_text = '\n'.join(lines[:10])
            id_match = ALL_DOC_ID_RE.search(search_text)
            if id_match:
                doc_id = id_match.group(1)
            return doc_type, doc_id
    
    return None, None


def split_into_documents(pages: List[PageData]) -> List[Document]:
    """
    Identify document boundaries by detecting document headers at page starts.
    Each new header starts a new document; subsequent pages without headers
    are continuation pages belonging to the current document.
    """
    documents = []
    current_doc = None
    
    print("📋 Splitting pages into documents...")
    
    for page in tqdm(pages, desc="Classifying pages"):
        if page.page_num <= 4:
            continue  # Skip cover + vendor master
        
        doc_type, doc_id = classify_page(page.text)
        
        if doc_type is not None:
            # New document starts here
            if current_doc:
                documents.append(current_doc)
            
            current_doc = Document(
                doc_type=doc_type,
                doc_id=doc_id or f"UNK-{page.page_num}",
                pages=[page.page_num],
                raw_text=page.text
            )
        elif current_doc:
            # Continuation of current document
            current_doc.pages.append(page.page_num)
            current_doc.raw_text += "\n\n" + page.text
        else:
            # Orphan page (before first document)
            current_doc = Document(
                doc_type=DocType.FILLER,
                doc_id=f"FILLER-{page.page_num}",
                pages=[page.page_num],
                raw_text=page.text
            )
    
    if current_doc:
        documents.append(current_doc)
    
    # Stats
    type_counts = {}
    for doc in documents:
        t = doc.doc_type.value
        type_counts[t] = type_counts.get(t, 0) + 1
    
    print(f"✅ Found {len(documents)} documents:")
    for t, c in sorted(type_counts.items()):
        print(f"   {t}: {c}")
    
    return documents


def extract_doc_ids_from_text(text: str) -> List[str]:
    """Extract all document IDs found in text."""
    return list(set(ALL_DOC_ID_RE.findall(text)))
