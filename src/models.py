"""
Data models for the Financial Gauntlet pipeline.
"""
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any
from enum import Enum


class DocType(Enum):
    INVOICE = "invoice"
    PURCHASE_ORDER = "purchase_order"
    BANK_STATEMENT = "bank_statement"
    EXPENSE_REPORT = "expense_report"
    CREDIT_NOTE = "credit_note"
    DEBIT_NOTE = "debit_note"
    RECEIPT = "receipt"
    QUOTATION = "quotation"
    DELIVERY_NOTE = "delivery_note"
    TERMS = "terms"
    COVER = "cover"
    VENDOR_MASTER = "vendor_master"
    FILLER = "filler"


@dataclass
class PageData:
    page_num: int  # 1-indexed
    text: str
    tables: List[List[List[str]]] = field(default_factory=list)


@dataclass
class LineItem:
    description: str = ""
    hsn_sac: str = ""
    qty: float = 0.0
    unit: str = ""
    rate: float = 0.0
    amount: float = 0.0
    tax_rate: float = 0.0
    tax_amount: float = 0.0
    hours: Optional[float] = None


@dataclass
class Invoice:
    doc_id: str = ""
    vendor_name: str = ""
    vendor_gstin: str = ""
    vendor_ifsc: str = ""
    vendor_state: str = ""
    buyer_name: str = ""
    buyer_gstin: str = ""
    date: str = ""
    po_ref: str = ""
    line_items: List[LineItem] = field(default_factory=list)
    subtotal: float = 0.0
    tax_total: float = 0.0
    grand_total: float = 0.0
    pages: List[int] = field(default_factory=list)
    raw_text: str = ""


@dataclass
class POLineItem:
    description: str = ""
    hsn_sac: str = ""
    qty: float = 0.0
    rate: float = 0.0
    amount: float = 0.0


@dataclass
class PurchaseOrder:
    doc_id: str = ""
    vendor_name: str = ""
    date: str = ""
    line_items: List[POLineItem] = field(default_factory=list)
    total: float = 0.0
    pages: List[int] = field(default_factory=list)
    raw_text: str = ""


@dataclass
class BankTransaction:
    date: str = ""
    description: str = ""
    vendor_ref: str = ""
    debit: float = 0.0
    credit: float = 0.0
    balance: float = 0.0
    payment_ref: str = ""


@dataclass
class BankStatement:
    doc_id: str = ""
    month: str = ""
    year: str = ""
    account_name: str = ""
    opening_balance: float = 0.0
    closing_balance: float = 0.0
    transactions: List[BankTransaction] = field(default_factory=list)
    pages: List[int] = field(default_factory=list)
    raw_text: str = ""


@dataclass
class ExpenseItem:
    date: str = ""
    description: str = ""
    category: str = ""
    amount: float = 0.0
    vendor_name: str = ""
    hotel_name: str = ""
    city: str = ""


@dataclass
class ExpenseReport:
    doc_id: str = ""
    employee_name: str = ""
    employee_id: str = ""
    period: str = ""
    department: str = ""
    line_items: List[ExpenseItem] = field(default_factory=list)
    total: float = 0.0
    pages: List[int] = field(default_factory=list)
    raw_text: str = ""


@dataclass
class CreditDebitNote:
    doc_id: str = ""
    note_type: str = ""  # "credit" or "debit"
    vendor_name: str = ""
    date: str = ""
    references: List[str] = field(default_factory=list)  # referenced invoice/note IDs
    amount: float = 0.0
    reason: str = ""
    pages: List[int] = field(default_factory=list)
    raw_text: str = ""


@dataclass 
class VendorEntry:
    name: str = ""
    gstin: str = ""
    state: str = ""
    bank: str = ""
    ifsc: str = ""


@dataclass
class Document:
    doc_type: DocType = DocType.FILLER
    doc_id: str = ""
    pages: List[int] = field(default_factory=list)
    raw_text: str = ""
    parsed: Any = None  # Will hold Invoice, PO, etc.


@dataclass
class Finding:
    finding_id: str = ""
    category: str = ""
    pages: List[int] = field(default_factory=list)
    document_refs: List[str] = field(default_factory=list)
    description: str = ""
    reported_value: str = ""
    correct_value: str = ""
    confidence: float = 1.0
