import sys, os
from src.parser import parse_all_documents
from src.extractor import split_into_documents
from src.tax_rates import get_expected_tax_rate
from src.models import DocType 
import pickle

pages = pickle.load(open('data/extracted_pages.pkl', 'rb'))
docs = split_into_documents(pages)
parse_all_documents(docs)

for doc in docs:
    if doc.doc_type == DocType.INVOICE and doc.parsed:
        inv = doc.parsed
        
        expected_tax = 0.0
        all_rates = []
        valid_hsns = True
        for item in inv.line_items:
            hsn = ''.join(filter(str.isalnum, item.hsn_sac))
            r = get_expected_tax_rate(hsn)
            if r < 0:
                valid_hsns = False
                break
            expected_tax += item.amount * (r / 100.0)
            all_rates.append(r)
        
        if not valid_hsns: continue
        
        actual_tax = inv.tax_total
        diff = abs(expected_tax - actual_tax)
        if diff > 1.0:
            eff_rate = round((inv.tax_total / inv.subtotal) * 100, 1) if inv.subtotal else 0
            
            # calculate tax if a single blanket rate was applied
            blanket_matches = []
            for b_rate in [5.0, 12.0, 18.0, 28.0]:
                if abs(inv.subtotal * b_rate / 100.0 - actual_tax) < 1.0:
                    blanket_matches.append(b_rate)
            
            print(f"{inv.doc_id}: diff={diff:.2f}, eff_rate={eff_rate}%, expected_sum={expected_tax:.2f}, calc={actual_tax:.2f}, blanket={blanket_matches}")

