# ---------------------------------------------------------------------------
# Toronto 311 Service Request Audit — Phase 0: Data Profiling
#
# Pulls the 311 dataset from Toronto's CKAN portal and prints the facts we
# need before writing any analysis: row count, status values, top request
# types, high-missing columns, and a small sample.
#
# The Toronto dataset ships as a ZIP of per-year CSVs — not a single CSV —
# so this script concatenates all years and validates column consistency.
# ---------------------------------------------------------------------------

import io
import json
import zipfile
from typing import Optional

import pandas as pd
import requests
from ckanapi import RemoteCKAN


# --- Configuration ---------------------------------------------------------
CKAN_URL = "https://ckan0.cf.opendata.inter.prod-toronto.ca"
PACKAGE_ID = "311-service-requests-customer-initiated"

# The combined ZIP is ~200MB; give slow connections time.
REQUEST_TIMEOUT = 180  # seconds

# Columns we *must* have for Phase 1 (backlog reconciliation).
# If any are missing, we fail fast with a clear message.
REQUIRED_COLUMNS = {"Status", "Service Request Type", "Creation Date"}


def list_zip_resources() -> None:
    """Print every ZIP resource in the package, so we can see year coverage."""
    ckan = RemoteCKAN(CKAN_URL)
    resources = ckan.action.package_show(id=PACKAGE_ID)["resources"]

    zips = [r for r in resources if (r.get("format") or "").upper() == "ZIP"]
    print(f"\n--- {len(zips)} ZIP resource(s) in package ---")
    for r in zips:
        print(f"  {r.get('name')!r}")
        print(f"    url: {r['url']}")

# --- Step 1: Locate a downloadable resource --------------------------------
def get_download_url() -> tuple[str, str]:
    """Return (url, format) for the 311 dataset.

    Prefers the ZIP (contains per-year CSVs) and falls back to XLSX.
    Raises LookupError with a helpful message if neither is available.
    """
    try:
        ckan = RemoteCKAN(CKAN_URL)
        resources = ckan.action.package_show(id=PACKAGE_ID).get("resources", [])
    except Exception as e:
        # CKAN call failed — network, DNS, or portal outage.
        raise RuntimeError(f"CKAN request failed: {e}") from e

    if not resources:
        raise LookupError(f"Package {PACKAGE_ID!r} returned no resources.")

    # Try ZIP first (raw CSVs, no Excel formatting), then XLSX.
    for fmt in ("ZIP", "XLSX"):
        match = next(
            (r for r in resources if (r.get("format") or "").upper() == fmt),
            None,
        )
        if match and match.get("url"):
            return match["url"], fmt

    # Neither format found — list what *is* there to help debug.
    available = sorted({(r.get("format") or "?").upper() for r in resources})
    raise LookupError(f"No ZIP or XLSX resource. Available formats: {available}")


# --- Step 2a: Load from ZIP (one CSV per year) -----------------------------
def _download(url: str) -> bytes:
    """Fetch bytes with an explicit timeout and HTTP status check."""
    try:
        resp = requests.get(url, timeout=REQUEST_TIMEOUT)
    except requests.RequestException as e:
        raise RuntimeError(f"Download failed: {e}") from e

    # `raise_for_status` turns 4xx/5xx into an exception instead of
    # silently returning an HTML error page that pandas would choke on.
    resp.raise_for_status()

    if not resp.content:
        raise RuntimeError(f"Empty response body from {url}")
    return resp.content


def load_from_zip(url: str) -> pd.DataFrame:
    """Read every CSV inside the ZIP and concatenate them.

    Toronto's CSVs are CP1252-encoded (French accents), not UTF-8.
    We try a chain of encodings and use the first one that decodes cleanly.
    """
    zip_bytes = _download(url)

    try:
        zf = zipfile.ZipFile(io.BytesIO(zip_bytes))
    except zipfile.BadZipFile as e:
        raise RuntimeError(f"Downloaded file is not a valid ZIP: {e}") from e

    with zf:
        csv_names = sorted(
            n for n in zf.namelist() if n.lower().endswith(".csv")
        )
        if not csv_names:
            raise LookupError("ZIP contains no CSV files.")

        frames = []
        base_columns: Optional[set] = None

        for name in csv_names:
            try:
                with zf.open(name) as f:
                    df_year = _read_csv_with_fallback(f, name)
            except Exception as e:
                print(f"  [warn] Skipping {name}: {e}")
                continue

            if df_year is None:
                print(f"  [warn] Skipping {name}: no encoding worked")
                continue

            cols = set(df_year.columns)
            if base_columns is None:
                base_columns = cols
            elif cols != base_columns:
                missing = base_columns - cols
                extra = cols - base_columns
                print(f"  [warn] Schema drift in {name}: "
                      f"missing={missing or 'none'}, extra={extra or 'none'}")

            df_year["__source_file"] = name
            frames.append(df_year)

        if not frames:
            raise RuntimeError("No CSVs could be read from the ZIP.")

        combined = pd.concat(frames, ignore_index=True, sort=False)
        print(f"  Loaded {len(frames)} CSV file(s) -> {len(combined):,} rows")
        return combined


def _read_csv_with_fallback(fileobj, name: str) -> Optional[pd.DataFrame]:
    """Try UTF-8, then CP1252, then Latin-1; return None if all fail.

    `fileobj` is a file-like object from the ZIP; pandas consumes it on
    the first attempt, so we re-open the CSV for each encoding by
    re-seeking is not possible — instead we read bytes once and try
    decoding each way.
    """
    raw = fileobj.read()
    if not raw:
        raise RuntimeError("empty file")

    for encoding in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            text = raw.decode(encoding)
        except UnicodeDecodeError:
            continue

        # `on_bad_lines="skip"` drops malformed rows instead of crashing;
        # this is what keeps the run alive when one row has an extra comma.
        df = pd.read_csv(
            io.StringIO(text),
            low_memory=False,
            on_bad_lines="skip",
        )
        print(f"  Read {name} as {encoding} -> {len(df):,} rows")
        return df

    return None


# --- Step 2b: Load from XLSX (fallback) ------------------------------------
def load_from_xlsx(url: str) -> pd.DataFrame:
    """Fallback path: read the XLSX directly.

    pandas needs an engine for this — openpyxl is already installed
    as a pandas dependency in most environments. We catch the error
    explicitly so the message is actionable.
    """
    try:
        return pd.read_excel(url, engine="openpyxl")
    except ImportError as e:
        raise RuntimeError(
            "Reading XLSX requires openpyxl. Install with: pip install openpyxl"
        ) from e


# --- Step 3: Profile the DataFrame -----------------------------------------
def profile(df: pd.DataFrame) -> None:
    """Print the key facts we need for the Phase 0 data profile."""
    # --- Size and schema ---
    print(f"\nRows: {len(df):,} | Columns: {df.shape[1]}")
    print(f"Columns: {list(df.columns)}")

    # --- Guard: required columns must exist before we go further ---
    missing = REQUIRED_COLUMNS - set(df.columns)
    if missing:
        raise RuntimeError(
            f"Required columns missing: {missing}. "
            f"Available: {sorted(df.columns)}"
        )

    # --- Status distribution — THE critical output for the audit ---
    # `dropna=False` keeps blank-status rows visible so we can't
    # underestimate data quality problems.
    print("\n--- Status counts ---")
    print(df["Status"].value_counts(dropna=False).to_string())

    # --- Top service types — these become rows of the reconciliation table ---
    print("\n--- Top 10 Service Request Types ---")
    print(df["Service Request Type"].value_counts().head(10).to_string())

    # --- Data-quality flags ---
    # Columns >20% missing can't support analysis; surfacing them now
    # prevents later "why is this chart empty?" confusion.
    print("\n--- Columns with >20% missing ---")
    missing_frac = df.isna().mean()
    high_missing = missing_frac[missing_frac > 0.20].round(3)
    print(high_missing.to_string() if not high_missing.empty else "(none)")

    # --- Date range sanity check ---
    # A wrong `Creation Date` range (e.g. future dates, 1900-01-01 sentinels)
    # is a common upstream issue that invalidates any "age of request" metric.
    print("\n--- Creation Date range ---")
    dates = pd.to_datetime(df["Creation Date"], errors="coerce")
    valid = dates.dropna()
    if valid.empty:
        print("  (no parseable dates)")
    else:
        print(f"  min: {valid.min()}")
        print(f"  max: {valid.max()}")
        print(f"  unparseable: {dates.isna().sum():,}")

    # --- Sample rows for eyeballing ---
    # `default=str` prevents json.dumps from crashing on Timestamp / NaN.
    print("\n--- Sample rows ---")
    print(json.dumps(df.head(3).to_dict("records"), indent=2, default=str))


# --- Entry point -----------------------------------------------------------
if __name__ == "__main__":
    list_zip_resources()
    try:
        url, fmt = get_download_url()
        print(f"Downloading {fmt} from: {url}")

        df = load_from_zip(url) if fmt == "ZIP" else load_from_xlsx(url)
        profile(df)

    except Exception as e:
        # Print a clean one-line error but keep the traceback for debugging.
        print(f"\nERROR: {e}")
        raise
