"""SQLite schema initialization for the Financial Gauntlet pipeline."""
import sqlite3
import os

DB_PATH = os.path.join(os.path.dirname(__file__), 'financial_gauntlet.db')


def get_connection(db_path: str = DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def init_db(db_path: str = DB_PATH):
    conn = get_connection(db_path)
    cur = conn.cursor()

    # ── Vendor Master ─────────────────────────────────────────────────────────
    cur.execute("""
        CREATE TABLE IF NOT EXISTS vendor_master (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            seq_no      INTEGER,
            name        TEXT NOT NULL,
            gstin       TEXT,
            state       TEXT,
            bank        TEXT,
            ifsc        TEXT
        )
    """)

    # ── Documents (segment index) ─────────────────────────────────────────────
    cur.execute("""
        CREATE TABLE IF NOT EXISTS documents (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            doc_type    TEXT NOT NULL,   -- TAX INVOICE, PURCHASE ORDER, etc.
            doc_ref     TEXT,            -- primary reference (invoice no / PO no / etc.)
            page_start  INTEGER NOT NULL,
            page_end    INTEGER NOT NULL
        )
    """)

    # ── Tax Invoices ──────────────────────────────────────────────────────────
    cur.execute("""
        CREATE TABLE IF NOT EXISTS invoices (
            invoice_no      TEXT PRIMARY KEY,
            po_ref          TEXT,
            date            TEXT,        -- YYYY-MM-DD
            vendor_name     TEXT,
            vendor_gstin    TEXT,
            vendor_ifsc     TEXT,
            subtotal        REAL,
            cgst            REAL,
            sgst            REAL,
            igst            REAL,
            grand_total     REAL,
            page_start      INTEGER,
            page_end        INTEGER
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS invoice_line_items (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            invoice_no  TEXT NOT NULL,
            line_num    INTEGER,
            description TEXT,
            hsn         TEXT,
            qty         REAL,
            unit        TEXT,
            rate        REAL,
            amount      REAL,
            FOREIGN KEY (invoice_no) REFERENCES invoices(invoice_no)
        )
    """)

    # ── Purchase Orders ───────────────────────────────────────────────────────
    cur.execute("""
        CREATE TABLE IF NOT EXISTS purchase_orders (
            po_no           TEXT PRIMARY KEY,
            date            TEXT,        -- YYYY-MM-DD
            delivery_date   TEXT,
            payment_terms   TEXT,
            vendor_name     TEXT,
            vendor_gstin    TEXT,
            subtotal        REAL,
            gst             REAL,
            total           REAL,
            page_start      INTEGER,
            page_end        INTEGER
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS po_line_items (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            po_no       TEXT NOT NULL,
            line_num    INTEGER,
            description TEXT,
            hsn         TEXT,
            qty         REAL,
            unit        TEXT,
            rate        REAL,
            amount      REAL,
            FOREIGN KEY (po_no) REFERENCES purchase_orders(po_no)
        )
    """)

    # ── Bank Statements ───────────────────────────────────────────────────────
    cur.execute("""
        CREATE TABLE IF NOT EXISTS bank_statements (
            stmt_id         TEXT PRIMARY KEY,
            account_name    TEXT,
            account_no      TEXT,
            bank            TEXT,
            ifsc            TEXT,
            period_start    TEXT,        -- YYYY-MM-DD
            period_end      TEXT,        -- YYYY-MM-DD
            opening_balance REAL,
            closing_balance REAL,
            page_start      INTEGER,
            page_end        INTEGER
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS bank_transactions (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            stmt_id     TEXT NOT NULL,
            txn_date    TEXT,
            description TEXT,
            txn_type    TEXT,
            ref         TEXT,
            debit       REAL,
            credit      REAL,
            balance     REAL,
            FOREIGN KEY (stmt_id) REFERENCES bank_statements(stmt_id)
        )
    """)

    # ── Expense Reports ───────────────────────────────────────────────────────
    cur.execute("""
        CREATE TABLE IF NOT EXISTS expense_reports (
            report_id       TEXT PRIMARY KEY,
            date            TEXT,        -- YYYY-MM-DD
            employee_name   TEXT,
            employee_id     TEXT,
            department      TEXT,
            purpose         TEXT,
            city            TEXT,
            total_claimed   REAL,
            page_start      INTEGER,
            page_end        INTEGER
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS expense_entries (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            report_id   TEXT NOT NULL,
            entry_date  TEXT,
            category    TEXT,
            description TEXT,
            city        TEXT,
            amount      REAL,
            FOREIGN KEY (report_id) REFERENCES expense_reports(report_id)
        )
    """)

    # ── Credit Notes ──────────────────────────────────────────────────────────
    cur.execute("""
        CREATE TABLE IF NOT EXISTS credit_notes (
            cn_no           TEXT PRIMARY KEY,
            date            TEXT,
            vendor_name     TEXT,
            vendor_gstin    TEXT,
            original_ref    TEXT,        -- references another document
            reason          TEXT,
            amount          REAL,
            page_start      INTEGER,
            page_end        INTEGER
        )
    """)

    # ── Debit Notes ───────────────────────────────────────────────────────────
    cur.execute("""
        CREATE TABLE IF NOT EXISTS debit_notes (
            dn_no           TEXT PRIMARY KEY,
            date            TEXT,
            vendor_name     TEXT,
            vendor_gstin    TEXT,
            original_ref    TEXT,
            reason          TEXT,
            amount          REAL,
            page_start      INTEGER,
            page_end        INTEGER
        )
    """)

    conn.commit()
    conn.close()
    print(f"[db_setup] Schema initialized at {db_path}")


if __name__ == "__main__":
    init_db()
