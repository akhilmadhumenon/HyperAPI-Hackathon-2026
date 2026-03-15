"""
Reference stores for cross-document lookups.
"""
import re
from typing import Dict, List, Optional, Tuple, Set
from collections import defaultdict
from rapidfuzz import fuzz, process

from .models import (
    Document, DocType, Invoice, PurchaseOrder, BankStatement,
    ExpenseReport, CreditDebitNote
)
from .vendor_master import VENDOR_MASTER, VENDOR_NAMES, VENDOR_BY_NAME
from .utils import parse_date_to_tuple


class PORegistry:
    """Purchase Order lookup registry."""
    
    def __init__(self):
        self.orders: Dict[str, PurchaseOrder] = {}
    
    def add(self, po: PurchaseOrder):
        self.orders[po.doc_id] = po
    
    def get(self, po_id: str) -> Optional[PurchaseOrder]:
        return self.orders.get(po_id)
    
    def exists(self, po_id: str) -> bool:
        return po_id in self.orders
    
    @property
    def all_ids(self) -> Set[str]:
        return set(self.orders.keys())


class InvoiceRegistry:
    """Invoice lookup and grouping."""
    
    def __init__(self):
        self.invoices: Dict[str, Invoice] = {}
        self.by_po: Dict[str, List[Invoice]] = defaultdict(list)
        self.by_vendor: Dict[str, List[Invoice]] = defaultdict(list)
    
    def add(self, inv: Invoice):
        if inv.doc_id in self.invoices:
            # Already added this document ID, don't duplicate in groupings
            return
            
        self.invoices[inv.doc_id] = inv
        if inv.po_ref:
            self.by_po[inv.po_ref].append(inv)
        if inv.vendor_name:
            self.by_vendor[inv.vendor_name.lower()].append(inv)
    
    def get(self, inv_id: str) -> Optional[Invoice]:
        return self.invoices.get(inv_id)
    
    def get_by_po(self, po_id: str) -> List[Invoice]:
        return self.by_po.get(po_id, [])


class BankStatementChain:
    """Bank statements ordered by month for balance tracking."""
    
    MONTH_ORDER = {
        'january': 1, 'february': 2, 'march': 3, 'april': 4,
        'may': 5, 'june': 6, 'july': 7, 'august': 8,
        'september': 9, 'october': 10, 'november': 11, 'december': 12,
        'jan': 1, 'feb': 2, 'mar': 3, 'apr': 4,
        'mar': 3, 'apr': 4, 'may': 5,
        'jun': 6, 'jul': 7, 'aug': 8, 'sep': 9,
        'oct': 10, 'nov': 11, 'dec': 12
    }
    
    def __init__(self):
        self.statements: List[BankStatement] = []
    
    def add(self, bs: BankStatement):
        self.statements.append(bs)
    
    def get_ordered(self) -> List[BankStatement]:
        """Return statements ordered by month."""
        def sort_key(bs: BankStatement):
            month_num = self.MONTH_ORDER.get(bs.month.lower(), 0)
            year_num = int(bs.year) if bs.year.isdigit() else 0
            return (year_num, month_num)
        
        return sorted(self.statements, key=sort_key)
    
    def get_all_transactions(self) -> List[Tuple[BankStatement, 'BankTransaction']]:
        """Get all transactions across all statements."""
        result = []
        for bs in self.statements:
            for txn in bs.transactions:
                result.append((bs, txn))
        return result


class ExpenseIndex:
    """Index expense items for duplicate/triple detection."""
    
    def __init__(self):
        self.reports: Dict[str, ExpenseReport] = {}
        self.items_by_key: Dict[str, List[Tuple[str, 'ExpenseItem']]] = defaultdict(list)
        self.items_by_hotel: Dict[str, List[Tuple[str, 'ExpenseItem']]] = defaultdict(list)
        self.by_employee_id: Dict[str, List[ExpenseReport]] = defaultdict(list)
    
    def add(self, er: ExpenseReport):
        self.reports[er.doc_id] = er
        if er.employee_id:
            self.by_employee_id[er.employee_id].append(er)
        
        for item in er.line_items:
            # 1. Exact Date + Amount for duplicates
            d_tuple = parse_date_to_tuple(item.date)
            d_key = f"{d_tuple[0]}-{d_tuple[1]:02d}-{d_tuple[2]:02d}" if d_tuple else item.date.lower().strip()
            key = f"{d_key}|{item.amount:.2f}"
            self.items_by_key[key].append((er.doc_id, item))
            
            # 2. Strict Amount + Name for Hotel triples (different dates!)
            desc = re.sub(r'[^a-z0-9\s]', '', item.description.lower())
            desc_parts = desc.split()
            # Strip common prefixes from PDF columns
            while desc_parts and desc_parts[0] in ('hotel', 'accommodatio', 'accommodation', 'flight', 'ticket'):
                desc_parts.pop(0)
            core_hotel = ' '.join(desc_parts[:4])
            
            if core_hotel:
                hotel_key = f"{core_hotel}|{item.amount:.2f}"
                self.items_by_hotel[hotel_key].append((er.doc_id, item))
    
    def find_duplicates(self) -> List[Tuple[str, List[Tuple[str, 'ExpenseItem']]]]:
        """Find expense items that appear in exactly 2 reports (same date and amount)."""
        return [(key, items) for key, items in self.items_by_key.items() if len(items) == 2]
    
    def find_triples(self) -> List[Tuple[str, List[Tuple[str, 'ExpenseItem']]]]:
        """Find hotel expense items claimed 3+ times (same hotel and amount, different dates)."""
        return [(key, items) for key, items in self.items_by_hotel.items() if len(items) >= 3]


class CreditDebitGraph:
    """Directed graph of credit/debit note references for cycle detection."""
    
    def __init__(self):
        self.notes: Dict[str, CreditDebitNote] = {}
        self.edges: Dict[str, List[str]] = defaultdict(list)  # from -> [to, ...]
    
    def add(self, note: CreditDebitNote):
        self.notes[note.doc_id] = note
        for ref in note.references:
            self.edges[note.doc_id].append(ref)
    
    def find_cycles(self) -> List[List[str]]:
        """Find circular references using a more robust DFS."""
        cycles = []
        
        def find_all_cycles(node, path, path_set):
            path.append(node)
            path_set.add(node)
            
            for neighbor in self.edges.get(node, []):
                if neighbor in path_set:
                    # Found a cycle
                    cycle_start = path.index(neighbor)
                    cycles.append(path[cycle_start:] + [neighbor])
                elif neighbor not in path: # Basic check to avoid infinite recursion
                    # We limit depth to avoid excessive searching in huge datasets
                    if len(path) < 10: 
                        find_all_cycles(neighbor, path, path_set)
                        
            path.pop()
            path_set.remove(node)

        for start_node in self.edges:
            find_all_cycles(start_node, [], set())
            
        return cycles
    
    def is_real_invoice_ref(self, ref: str) -> bool:
        """Check if a reference points to a real invoice (not just another note)."""
        return ref.startswith("INV-") and ref not in self.notes


class VendorMatcher:
    """Fuzzy vendor name matching against Vendor Master."""
    
    def __init__(self):
        self.names = VENDOR_NAMES
        self.names_lower = [n.lower() for n in self.names]
    
    def match(self, name: str, threshold: int = 80) -> Optional[Tuple[str, int]]:
        """
        Find best matching vendor name.
        Returns (matched_name, score) or None.
        """
        if not name:
            return None
        
        result = process.extractOne(
            name.lower(),
            self.names_lower,
            scorer=fuzz.token_sort_ratio,
            score_cutoff=threshold
        )
        
        if result:
            matched_lower, score, idx = result
            return (self.names[idx], score)
        return None
    
    def is_exact_match(self, name: str) -> bool:
        """Check if name exactly matches a vendor in master."""
        return name.lower().strip() in [n.lower() for n in self.names]


def build_reference_stores(documents: List[Document]) -> dict:
    """Build all reference stores from parsed documents."""
    print("📚 Building reference stores...")
    
    po_registry = PORegistry()
    invoice_registry = InvoiceRegistry()
    bank_chain = BankStatementChain()
    expense_index = ExpenseIndex()
    cd_graph = CreditDebitGraph()
    vendor_matcher = VendorMatcher()
    
    for doc in documents:
        if not doc.parsed:
            continue
        
        if doc.doc_type == DocType.PURCHASE_ORDER:
            po_registry.add(doc.parsed)
        elif doc.doc_type == DocType.INVOICE:
            invoice_registry.add(doc.parsed)
        elif doc.doc_type == DocType.BANK_STATEMENT:
            bank_chain.add(doc.parsed)
        elif doc.doc_type == DocType.EXPENSE_REPORT:
            expense_index.add(doc.parsed)
        elif doc.doc_type in (DocType.CREDIT_NOTE, DocType.DEBIT_NOTE):
            cd_graph.add(doc.parsed)
    
    print(f"  POs: {len(po_registry.orders)}")
    print(f"  Invoices: {len(invoice_registry.invoices)}")
    print(f"  Bank Statements: {len(bank_chain.statements)}")
    print(f"  Expense Reports: {len(expense_index.reports)}")
    print(f"  Credit/Debit Notes: {len(cd_graph.notes)}")
    
    return {
        'po_registry': po_registry,
        'invoice_registry': invoice_registry,
        'bank_chain': bank_chain,
        'expense_index': expense_index,
        'cd_graph': cd_graph,
        'vendor_matcher': vendor_matcher,
    }
