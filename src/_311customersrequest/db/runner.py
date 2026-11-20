import sys
from pathlib import Path
import duckdb

PROJECT_ROOT = Path(__file__).resolve().parents[3]
DB_PATH = PROJECT_ROOT / "toronto_311.duckdb"
SQL_FILE = PROJECT_ROOT / "queries" / "01_backlog_reconciliation.sql"


def run():
    if not DB_PATH.exists():
        raise FileNotFoundError(f"Database missing! Looked in: {DB_PATH}")

    if not SQL_FILE.exists():
        raise FileNotFoundError(f"SQL file missing! Looked in: {SQL_FILE}")

    with open(SQL_FILE, "r") as f:
        query = f.read()

    con = duckdb.connect(str(DB_PATH), read_only=True)
    try:
        result = con.execute(query)
        df = result.fetchdf()

        print(f"\n=== Backlog Reconciliation (Top 15) ===")
        print(df.head(15).to_string(index=False))

        print(f"\n=== Totals Across All Categories ===")
        # Use the new column names from the SQL
        print(f"Total Active Requests: {df['active_requests'].sum():,}")
        print(f"Total Aged Backlog (>90d): {df['aged_backlog_90d'].sum():,}")

    finally:
        con.close()


if __name__ == "__main__":
    run()
