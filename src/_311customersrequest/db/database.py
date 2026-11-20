# ---------------------------------------------------------------------------
# Toronto 311 Service Request Audit — Persistence Layer (DuckDB)
#
# Imports retrieval functions from profile.py — does NOT reimplement them.
# profile.py owns download + decode + profile; this file owns storage.
#
# DuckDB chosen over SQLite/Postgres:
#   - Columnar storage: GROUP BY scans only the needed column.
#   - Embedded: one file, no server, no credentials.
#   - Direct pandas integration: SELECT * FROM df works.
# ---------------------------------------------------------------------------

from pathlib import Path

import sys
import duckdb
import pandas as pd

# Import from the sibling profile module. Two forms:
#   - relative import when run as part of the package
#   - plain import when run as a script (`python database.py`)
# --- Fix the import path ---------------------------------------------------
# 1. __file__ is /.../_311customersrequest/db/database.py
# 2. .parent is /.../_311customersrequest/db
# 3. .parent.parent is /.../_311customersrequest
PACKAGE_ROOT = Path(__file__).resolve().parent.parent

# Add the root to sys.path so Python can find the "apis" folder
if str(PACKAGE_ROOT) not in sys.path:
    sys.path.insert(0, str(PACKAGE_ROOT))

# Now this will work correctly
try:
    from _311customersrequest.apis.profile import get_download_url, load_from_zip, load_from_xlsx
except ImportError as e:
    raise ImportError(
        f"Failed to import from 'apis.profile'.\n"
        f"Checked path: {PACKAGE_ROOT}\n"
        f"Make sure 'apis/profile.py' exists and contains the required functions.\n"
        f"Original error: {e}"
    ) from e



# Project root: .../src/_311customersrequest/ckanapis/database.py -> .../
DB_PATH = Path(__file__).resolve().parents[3] / "toronto_311.duckdb"

# Must exist before any write — the audit is meaningless without these.
REQUIRED_COLUMNS = {"Status", "Service Request Type", "Creation Date"}


def load_into_duckdb(df: pd.DataFrame, table_name: str = "service_requests_2026") -> None:
    """Write df to a DuckDB table, replacing any prior copy.

    CREATE OR REPLACE makes this idempotent — re-runs never duplicate rows.
    The finally block guarantees the file lock is released even on failure.
    """
    missing = REQUIRED_COLUMNS - set(df.columns)
    if missing:
        raise RuntimeError(f"Required columns missing: {missing}")

    DB_PATH.parent.mkdir(parents=True, exist_ok=True)

    con = duckdb.connect(str(DB_PATH))
    try:
        # `SELECT * FROM df` — DuckDB reads the pandas DataFrame directly.
        con.execute(f"CREATE OR REPLACE TABLE {table_name} AS SELECT * FROM df")
        rows = con.execute(f"SELECT COUNT(*) FROM {table_name}").fetchone()[0]
        print(f"  Wrote {rows:,} rows -> {DB_PATH.name}:{table_name}")
    finally:
        con.close()


def verify(table_name: str = "service_requests_2026") -> None:
    """Read back from a fresh connection to prove the write landed.

    A successful write in the writer process is not proof —
    a read through a new connection is.
    """
    con = duckdb.connect(str(DB_PATH), read_only=True)
    try:
        print("\n--- Status counts (from DB) ---")
        print(con.execute(
            f'SELECT "Status", COUNT(*) AS n FROM {table_name} '
            f'GROUP BY 1 ORDER BY n DESC'
        ).fetchdf().to_string())

        print("\n--- Creation Date range (from DB) ---")
        print(con.execute(
            f'SELECT MIN("Creation Date") AS min_dt, '
            f'MAX("Creation Date") AS max_dt FROM {table_name}'
        ).fetchdf().to_string())
    finally:
        con.close()


if __name__ == "__main__":
    try:
        # Retrieval delegated to profile.py — not reimplemented here.
        url, fmt = get_download_url()
        print(f"Downloading {fmt} from: {url}")

        df = load_from_zip(url) if fmt == "ZIP" else load_from_xlsx(url)

        # Persistence — the only thing this file adds.
        load_into_duckdb(df, "service_requests_2026")
        verify("service_requests_2026")

        print(f"\nDone. Query it with: duckdb {DB_PATH}")
    except Exception as e:
        print(f"\nERROR: {e}")
        raise
