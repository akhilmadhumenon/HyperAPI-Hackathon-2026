import pdfplumber
import sys

pdf_path = "/Users/thushara/Desktop/hyperbots- antigravity/gauntlet.pdf" # Guessing name from context
if len(sys.argv) > 1:
    pdf_path = sys.argv[1]

try:
    with pdfplumber.open(pdf_path) as pdf:
        # Check pages 830-850 where bank statements seem to be
        for i in range(830, 850):
            if i < len(pdf.pages):
                print(f"--- Page {i+1} ---")
                print(pdf.pages[i].extract_text())
                print("\n")
except Exception as e:
    print(f"Error: {e}")
