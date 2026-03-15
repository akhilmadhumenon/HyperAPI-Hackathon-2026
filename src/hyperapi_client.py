"""
HyperAPI client wrapper for document intelligence operations.
"""
import os
import requests
import time
import json
from typing import Optional, Dict, Any, List
from pathlib import Path


class HyperAPIClient:
    """Client for HyperAPI document intelligence endpoints."""
    
    def __init__(self, api_key: str = None, base_url: str = None):
        self.api_key = api_key or os.environ.get("HYPERAPI_KEY", "")
        self.base_url = (base_url or os.environ.get("HYPERAPI_BASE_URL", "https://apis.hyperbots.com/api/v1")).rstrip("/")
        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": f"Bearer {self.api_key}",
            "X-API-Key": self.api_key,
        })
    
    def parse(self, file_path: str, pages: Optional[List[int]] = None) -> Dict[str, Any]:
        """
        Parse/OCR a document — extract text from PDF/images.
        
        Args:
            file_path: Path to the PDF file
            pages: Optional list of specific page numbers to parse
        """
        url = f"{self.base_url}/parse"
        
        with open(file_path, "rb") as f:
            files = {"file": (Path(file_path).name, f, "application/pdf")}
            data = {}
            if pages:
                data["pages"] = json.dumps(pages)
            
            response = self.session.post(url, files=files, data=data, timeout=300)
        
        response.raise_for_status()
        return response.json()
    
    def classify(self, file_path: str) -> Dict[str, Any]:
        """
        Classify document type (invoice, PO, receipt, etc).
        """
        url = f"{self.base_url}/classify"
        
        with open(file_path, "rb") as f:
            files = {"file": (Path(file_path).name, f, "application/pdf")}
            response = self.session.post(url, files=files, timeout=60)
        
        response.raise_for_status()
        return response.json()
    
    def split(self, file_path: str) -> Dict[str, Any]:
        """
        Split multi-page document into logical sections.
        """
        url = f"{self.base_url}/split"
        
        with open(file_path, "rb") as f:
            files = {"file": (Path(file_path).name, f, "application/pdf")}
            response = self.session.post(url, files=files, timeout=300)
        
        response.raise_for_status()
        return response.json()
    
    def extract(self, file_path: str, schema: Optional[Dict] = None) -> Dict[str, Any]:
        """
        Extract structured data fields from document.
        
        Args:
            file_path: Path to the PDF file
            schema: Optional JSON schema to guide extraction
        """
        url = f"{self.base_url}/extract"
        
        with open(file_path, "rb") as f:
            files = {"file": (Path(file_path).name, f, "application/pdf")}
            data = {}
            if schema:
                data["schema"] = json.dumps(schema)
            
            response = self.session.post(url, files=files, data=data, timeout=300)
        
        response.raise_for_status()
        return response.json()
    
    def parse_pages(self, file_path: str, page_ranges: List[tuple] = None, 
                     batch_size: int = 50) -> List[Dict]:
        """
        Parse document in batches of pages.
        Returns list of parsed results.
        """
        results = []
        if page_ranges:
            for start, end in page_ranges:
                pages = list(range(start, end + 1))
                try:
                    result = self.parse(file_path, pages=pages)
                    results.append(result)
                except Exception as e:
                    print(f"  Error parsing pages {start}-{end}: {e}")
                    results.append({"error": str(e), "pages": pages})
                time.sleep(0.5)  # Rate limiting
        else:
            result = self.parse(file_path)
            results.append(result)
        
        return results
