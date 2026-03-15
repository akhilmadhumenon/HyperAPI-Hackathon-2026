"""
LLM-enhanced document parsing for when regex-based parsing fails.
Uses OpenAI GPT-4o-mini or HyperAPI Extract for structured extraction.
"""
import os
import json
import time
from typing import List, Optional, Dict, Any
from .models import (
    Document, DocType, Invoice, LineItem, PurchaseOrder, POLineItem,
    BankStatement, BankTransaction, ExpenseReport, ExpenseItem,
    CreditDebitNote
)
from .parser import parse_number


def get_openai_client():
    """Get OpenAI client if available."""
    try:
        from openai import OpenAI
        api_key = os.environ.get("OPENAI_API_KEY", "")
        if api_key:
            return OpenAI(api_key=api_key)
    except ImportError:
        pass
    return None


INVOICE_SCHEMA = """Extract these fields from the invoice text as JSON:
{
    "vendor_name": "string",
    "vendor_gstin": "string (15 char GSTIN)",
    "vendor_ifsc": "string (IFSC code)",
    "vendor_state": "string",
    "date": "string (DD/MM/YYYY)",
    "po_ref": "string (PO-XXXX-XXXX format)",
    "line_items": [
        {
            "description": "string",
            "hsn_sac": "string",
            "qty": number,
            "unit": "string",
            "rate": number,
            "amount": number,
            "tax_rate": number,
            "tax_amount": number
        }
    ],
    "subtotal": number,
    "tax_total": number,
    "grand_total": number
}"""


PO_SCHEMA = """Extract these fields from the Purchase Order text as JSON:
{
    "vendor_name": "string",
    "date": "string (DD/MM/YYYY)",
    "line_items": [
        {
            "description": "string",
            "qty": number,
            "rate": number,
            "amount": number
        }
    ],
    "total": number
}"""


BS_SCHEMA = """Extract these fields from the Bank Statement text as JSON:
{
    "month": "string (month name)",
    "year": "string (YYYY)",
    "account_name": "string (account number or name)",
    "opening_balance": number,
    "closing_balance": number,
    "transactions": [
        {
            "date": "string",
            "description": "string",
            "debit": number,
            "credit": number,
            "balance": number,
            "payment_ref": "string"
        }
    ]
}"""


ER_SCHEMA = """Extract these fields from the Expense Report text as JSON:
{
    "employee_name": "string",
    "employee_id": "string",
    "department": "string",
    "period": "string",
    "line_items": [
        {
            "date": "string",
            "description": "string",
            "category": "string",
            "amount": number,
            "hotel_name": "string or null"
        }
    ],
    "total": number
}"""


CN_DN_SCHEMA = """Extract these fields from the Credit/Debit Note text as JSON:
{
    "vendor_name": "string",
    "date": "string",
    "references": ["list of referenced document IDs like INV-XXXX-XXXX, CN-XXXX-XXXX"],
    "amount": number,
    "reason": "string"
}"""


def llm_parse_document(doc: Document, client=None) -> Optional[Any]:
    """
    Use LLM to parse a document when regex-based parsing may be insufficient.
    """
    if client is None:
        client = get_openai_client()
    
    if client is None:
        return None
    
    schema_map = {
        DocType.INVOICE: INVOICE_SCHEMA,
        DocType.PURCHASE_ORDER: PO_SCHEMA,
        DocType.BANK_STATEMENT: BS_SCHEMA,
        DocType.EXPENSE_REPORT: ER_SCHEMA,
        DocType.CREDIT_NOTE: CN_DN_SCHEMA,
        DocType.DEBIT_NOTE: CN_DN_SCHEMA,
    }
    
    schema = schema_map.get(doc.doc_type)
    if not schema:
        return None
    
    # Prepare text with page demarcation to help LLM follow "vendor from first page" rule
    pages_text = doc.raw_text.split('\n\n')
    demarcated_text = f"--- PAGE 1 (EXTRACT VENDOR/HEADER INFO FROM HERE) ---\n{pages_text[0]}"
    if len(pages_text) > 1:
        demarcated_text += "\n\n--- CONTINUATION PAGES (USE FOR LINE ITEMS/TOTALS) ---\n" + "\n\n".join(pages_text[1:])
    
    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": f"You are a financial document parser. Extract structured data from the document text. {schema}\n\nIMPORTANT: For multi-page documents, the vendor/header information MUST be extracted from the first page only. Return ONLY the JSON, no markdown formatting."},
                {"role": "user", "content": f"Document ID: {doc.doc_id}\nDocument Type: {doc.doc_type.value}\n\n{demarcated_text[:12000]}"}
            ],
            temperature=0,
            max_tokens=4000,
        )
        
        result_text = response.choices[0].message.content.strip()
        # Clean markdown formatting if present
        if result_text.startswith("```"):
            result_text = result_text.split("\n", 1)[1]
            result_text = result_text.rsplit("```", 1)[0]
        
        data = json.loads(result_text)
        return _convert_llm_result(doc, data)
        
    except Exception as e:
        print(f"  ⚠️ LLM parse failed for {doc.doc_id}: {e}")
        return None


def _convert_llm_result(doc: Document, data: Dict) -> Any:
    """Convert LLM JSON result to proper data model."""
    
    if doc.doc_type == DocType.INVOICE:
        inv = Invoice(
            doc_id=doc.doc_id,
            pages=doc.pages,
            raw_text=doc.raw_text,
            vendor_name=data.get("vendor_name", ""),
            vendor_gstin=data.get("vendor_gstin", ""),
            vendor_ifsc=data.get("vendor_ifsc", ""),
            vendor_state=data.get("vendor_state", ""),
            date=data.get("date", ""),
            po_ref=data.get("po_ref", ""),
            subtotal=parse_number(str(data.get("subtotal", 0))),
            tax_total=parse_number(str(data.get("tax_total", 0))),
            grand_total=parse_number(str(data.get("grand_total", 0))),
        )
        for item_data in data.get("line_items", []):
            inv.line_items.append(LineItem(
                description=item_data.get("description", ""),
                hsn_sac=str(item_data.get("hsn_sac", "")),
                qty=parse_number(str(item_data.get("qty", 0))),
                unit=item_data.get("unit", ""),
                rate=parse_number(str(item_data.get("rate", 0))),
                amount=parse_number(str(item_data.get("amount", 0))),
                tax_rate=parse_number(str(item_data.get("tax_rate", 0))),
                tax_amount=parse_number(str(item_data.get("tax_amount", 0))),
            ))
        return inv
    
    elif doc.doc_type == DocType.PURCHASE_ORDER:
        po = PurchaseOrder(
            doc_id=doc.doc_id,
            pages=doc.pages,
            raw_text=doc.raw_text,
            vendor_name=data.get("vendor_name", ""),
            date=data.get("date", ""),
            total=parse_number(str(data.get("total", 0))),
        )
        for item_data in data.get("line_items", []):
            po.line_items.append(POLineItem(
                description=item_data.get("description", ""),
                qty=parse_number(str(item_data.get("qty", 0))),
                rate=parse_number(str(item_data.get("rate", 0))),
                amount=parse_number(str(item_data.get("amount", 0))),
            ))
        return po
    
    elif doc.doc_type == DocType.BANK_STATEMENT:
        bs = BankStatement(
            doc_id=doc.doc_id,
            pages=doc.pages,
            raw_text=doc.raw_text,
            month=data.get("month", ""),
            year=str(data.get("year", "")),
            account_name=data.get("account_name", ""),
            opening_balance=parse_number(str(data.get("opening_balance", 0))),
            closing_balance=parse_number(str(data.get("closing_balance", 0))),
        )
        for txn_data in data.get("transactions", []):
            bs.transactions.append(BankTransaction(
                date=txn_data.get("date", ""),
                description=txn_data.get("description", ""),
                debit=parse_number(str(txn_data.get("debit", 0))),
                credit=parse_number(str(txn_data.get("credit", 0))),
                balance=parse_number(str(txn_data.get("balance", 0))),
                payment_ref=txn_data.get("payment_ref", ""),
            ))
        return bs
    
    elif doc.doc_type == DocType.EXPENSE_REPORT:
        er = ExpenseReport(
            doc_id=doc.doc_id,
            pages=doc.pages,
            raw_text=doc.raw_text,
            employee_name=data.get("employee_name", ""),
            employee_id=data.get("employee_id", ""),
            department=data.get("department", ""),
            period=data.get("period", ""),
            total=parse_number(str(data.get("total", 0))),
        )
        for item_data in data.get("line_items", []):
            er.line_items.append(ExpenseItem(
                date=item_data.get("date", ""),
                description=item_data.get("description", ""),
                category=item_data.get("category", ""),
                amount=parse_number(str(item_data.get("amount", 0))),
                hotel_name=item_data.get("hotel_name", "") or "",
            ))
        return er
    
    elif doc.doc_type in (DocType.CREDIT_NOTE, DocType.DEBIT_NOTE):
        note = CreditDebitNote(
            doc_id=doc.doc_id,
            pages=doc.pages,
            raw_text=doc.raw_text,
            note_type="credit" if doc.doc_type == DocType.CREDIT_NOTE else "debit",
            vendor_name=data.get("vendor_name", ""),
            date=data.get("date", ""),
            references=data.get("references", []),
            amount=parse_number(str(data.get("amount", 0))),
            reason=data.get("reason", ""),
        )
        return note
    
    return None


def enhance_parsing_with_llm(documents: List[Document], use_llm: bool = True) -> List[Document]:
    """
    Re-parse documents that have poor regex extraction using LLM.
    Only re-parses if key fields are missing.
    """
    if not use_llm:
        print("⏭️ LLM enhancement skipped")
        return documents
    
    client = get_openai_client()
    if client is None:
        print("⏭️ No OpenAI API key — skipping LLM enhancement")
        return documents
    
    print("\n🤖 Enhancing extraction with LLM...")
    enhanced = 0
    
    for doc in documents:
        if not doc.parsed:
            continue
        
        needs_enhancement = False
        
        if doc.doc_type == DocType.INVOICE:
            inv = doc.parsed
            if not inv.vendor_name or not inv.line_items or inv.grand_total == 0:
                needs_enhancement = True
        
        elif doc.doc_type == DocType.PURCHASE_ORDER:
            po = doc.parsed
            if not po.vendor_name or not po.line_items:
                needs_enhancement = True
        
        elif doc.doc_type == DocType.BANK_STATEMENT:
            bs = doc.parsed
            if bs.opening_balance == 0 and bs.closing_balance == 0:
                needs_enhancement = True
        
        elif doc.doc_type == DocType.EXPENSE_REPORT:
            er = doc.parsed
            if not er.employee_id or not er.line_items:
                needs_enhancement = True
        
        if needs_enhancement:
            result = llm_parse_document(doc, client)
            if result:
                doc.parsed = result
                enhanced += 1
            time.sleep(0.3)  # Rate limiting
    
    print(f"✅ Enhanced {enhanced} documents with LLM")
    return documents
