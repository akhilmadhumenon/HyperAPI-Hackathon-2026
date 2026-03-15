# Financial Gauntlet — Error Detection Pipeline

This project is an automated pipeline designed to process a 1000-page document bundle (PDF) and detect up to 200 deliberate errors across 20 categories, tiered by difficulty (Easy, Medium, and Evil).

## 🏗️ Architecture

The pipeline consists of five major phases:

1.  **Phase 1: PDF Extraction**: Uses `pdfplumber` to extract text from each page. Extracted text is cached in `data/extracted_pages.pkl` for faster subsequent runs.
2.  **Phase 2: Document Parsing**: Segregates pages into logical documents (Invoices, Purchase Orders, Employee Records, etc.) and parses relevant fields. It optionally uses an LLM (OpenAI) to enhance parsing of complex or poorly extracted documents.
3.  **Phase 3: Reference Stores**: Builds cross-reference indexes (Vendor Master, PO Store, Employee Store) to enable complex checks like quantity accumulation and price escalation.
4.  **Phase 4: Error Detection**: Runs specialized detectors across three tiers:
    *   **Easy**: Basic checks like mathematical errors and date mismatches.
    *   **Medium**: Cross-document checks like PO/Invoice mismatches and quantity accumulation.
    *   **Evil**: Complex logical errors like date cascades and price escalations.
5.  **Phase 5: Assembly**: Aggregates all findings into a standardized `submission.json` format for evaluation.

## 🚀 How to Run

### Prerequisites

1.  Python 3.8+
2.  Install dependencies:
    ```bash
    pip install -r requirements.txt
    ```
3.  Set up environment variables in a `.env` file (see `.env.example`):
    ```env
    OPENAI_API_KEY=your_key_here
    TEAM_ID=your_team_id
    ```

### Execution

Run the main pipeline:
```bash
python main.py --pdf invoices.pdf --output submission.json --team-id <YOUR_TEAM_ID>
```

#### Options:
- `--use-llm`: Enable LLM-backed parsing for higher accuracy.
- `--force-extract`: Force re-extraction of the PDF (ignore cache).
- `--max-pages <N>`: Process only the first N pages for testing.

## 📁 Project Structure

- `main.py`: Entry point for the pipeline.
- `src/`:
    - `extractor.py`: PDF text extraction and document splitting.
    - `parser.py`: Field extraction from raw text.
    - `llm_parser.py`: LLM-based field enhancement.
    - `detectors/`: Logic for all error detection tiers.
    - `reference_store.py`: Cross-document data indexing.
    - `models.py`: Data structures for findings and documents.
- `data/`: (Generated) Cached data and intermediate analysis.
