import os
import requests
import json
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

api_key = os.environ.get("HYPERAPI_KEY")
base_url = os.environ.get("HYPERAPI_BASE_URL", "https://apis.hyperbots.com/api/v1").rstrip("/")

def test_parse():
    print(f"Testing HyperAPI at {base_url}...")
    url = f"{base_url}/parse"
    
    # We need a small PDF file or just send one page from gauntlet.pdf
    # For now, let's just check the health/auth if possible, but the user said POST /api/v1/parse
    
    # Let's create a dummy 1-page PDF for testing if possible, or just use page 5
    pdf_path = "test_page.pdf"
    import pdfplumber
    with pdfplumber.open("invoices.pdf") as pdf:
        with pdfplumber.open("invoices.pdf") as source:
             # Just a hacky way to get a small file: we can't easily save a new PDF without a library like fpdf or reportlab
             # but we can just use the tool to extract a page if we had a tool.
             # Actually I can just use a small slice of the file.
             pass

    # Let's try to parse the first 1000 bytes of gauntlet.pdf as a dummy test (it might fail but we see error msg)
    # Better: just use the real file but tell it to parse 1 page.
    
    files = {"file": ("invoices.pdf", open("invoices.pdf", "rb"), "application/pdf")}
    data = {"pages": json.dumps([5])}
    headers = {"X-API-Key": api_key, "Authorization": f"Bearer {api_key}"}
    
    try:
        response = requests.post(url, files=files, data=data, headers=headers, timeout=30)
        print(f"Status: {response.status_code}")
        if response.status_code == 200:
            result = response.json()
            print("Successfully parsed page 5!")
            # print(json.dumps(result, indent=2)[:500])
        else:
            print(f"Error: {response.text}")
    except Exception as e:
        print(f"Exception: {e}")

if __name__ == "__main__":
    test_parse()
