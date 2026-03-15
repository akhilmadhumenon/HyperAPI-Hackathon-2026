# HyperAPI Financial Gauntlet — Financial Detection Presentation
## Team baby_sharks

### 1. Objective
Build a high-precision document intelligence pipeline that detects 20 categories of financial inconsistencies from a 1,000-page dataset.

### 2. Architecture
- **PDF Splitting:** Deterministic header-based logic (pypdf).
- **Extraction:** AI-powered structured extraction (HyperAPI).
- **Database:** Normalized SQLite Relational Storage.
- **Validation:** Deterministic SQL Query Engine.

### 3. Error Categories & Logic

| Tier | Category | Logic Strategy |
| :--- | :--- | :--- |
| **Easy** | Arithmetic Error | `(qty * rate) != amount` or `(subtotal + tax) != total` |
| **Easy** | Billing Typo | Detects .30 vs .50 hour multiplier errors in billing. |
| **Easy** | Duplicate Line | Repeated items within a single invoice ID. |
| **Easy** | Invalid Date | Logical date validator (Feb 31, non-leap Feb 29). |
| **Medium** | PO Mismatch | Joins Invoice to PO; flags rate/qty variance > 1%. |
| **Medium** | Vendor Typo | Fuzzy match names against the official Vendor Master. |
| **Medium** | Double Payment | Identical debit refs/amounts across different bank cycles. |
| **Medium** | State Mismatch | Cross-check GSTIN state code vs Address State. |
| **Evil** | Qty Accumulation | `SUM(invoices) > 1.2 * PO_Contract_Qty`. |
| **Evil** | Price Escalation | monitors rate drift across sequence of invoices. |
| **Evil** | Balance Drift | `Month[N].Closing != Month[N+1].Opening` in bank chain. |
| **Evil** | Circular Ref | Graph cycles in Credit/Debit notes with no Invoice root. |
| **Evil** | Fake Vendor | Invoices from vendors missing from Master or with ghost GSTINs. |

### 4. Optimization
- **Caching:** Local JSON cache per page to handle API timeouts.
- **High Precision:** Manual verification of Vendor Master to eliminate false positives.
- **SQL-First:** Logic implemented as declarative queries for speed and stability.
