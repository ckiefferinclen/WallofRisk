from __future__ import annotations
import io
import re
import requests
import pandas as pd

# Current BTS Monthly TEU Tableau view. We request its CSV export rather than
# the retired 2022 Socrata table used in the prior connector.
TABLEAU_CSV_URLS = [
    "https://explore.dot.gov/t/BTS/views/MonthlyTEUDashboardNew_17697067597260/MonthlyData.csv?:showVizHome=no",
    "https://explore.dot.gov/t/BTS/views/MonthlyTEUDashboardNew_17697067597260/MonthlyData.csv?:showVizHome=no&:showTabs=true",
]

# Legacy table retained only as an explicit fallback. The app will label it
# stale because its underlying observations end in 2022.
LEGACY_SOCRATA_URL = "https://data.bts.gov/resource/rd72-aq8r.json?$limit=5000"

PORT_ALIASES = {
    "charleston": "Charleston, SC",
    "houston": "Houston, TX",
    "long beach": "Long Beach, CA",
    "los angeles": "Los Angeles, CA",
    "nwsa": "Seattle/Tacoma, WA",
    "seattle": "Seattle/Tacoma, WA",
    "tacoma": "Seattle/Tacoma, WA",
    "oakland": "Oakland, CA",
    "new york": "New York/New Jersey",
    "ny/nj": "New York/New Jersey",
    "ny & nj": "New York/New Jersey",
    "virginia": "Virginia",
    "savannah": "Savannah, GA",
}

LEGACY_PORT_COLUMNS = {
    "charleston_sc": "Charleston, SC",
    "houston_tx": "Houston, TX",
    "long_beach_ca": "Long Beach, CA",
    "los_angeles_ca": "Los Angeles, CA",
    "nwsa_seattle_tacoma_wa": "Seattle/Tacoma, WA",
    "oakland_ca": "Oakland, CA",
    "port_of_ny_nj": "New York/New Jersey",
    "port_of_virginia_va": "Virginia",
    "savannah_ga": "Savannah, GA",
}

def _clean_port(value: object) -> str | None:
    text = re.sub(r"\s+", " ", str(value)).strip()
    low = text.lower()
    for key, label in PORT_ALIASES.items():
        if key in low:
            return label
    return text if text and text.lower() not in {"nan", "total"} else None

def _parse_date(series: pd.Series) -> pd.Series:
    # Handles ordinary dates plus labels such as Jan 2026, 2026-01, and 1/2026.
    return pd.to_datetime(series.astype(str).str.strip(), errors="coerce")

def _parse_tableau_csv(content: bytes) -> pd.DataFrame:
    df = pd.read_csv(io.BytesIO(content))
    if df.empty:
        return pd.DataFrame(columns=["date", "port", "value"])
    df.columns = [str(c).strip() for c in df.columns]
    lower = {c: c.lower() for c in df.columns}

    # Prefer a long table with explicit port, period and TEU columns.
    port_col = next((c for c, l in lower.items() if "port" in l), None)
    date_col = next((c for c, l in lower.items() if any(k in l for k in ("date", "month", "period"))), None)
    value_col = next((c for c, l in lower.items() if "teu" in l and not any(k in l for k in ("port", "month", "date"))), None)
    if port_col and date_col and value_col:
        out = pd.DataFrame({
            "date": _parse_date(df[date_col]),
            "port": df[port_col].map(_clean_port),
            "value": pd.to_numeric(df[value_col].astype(str).str.replace(",", "", regex=False), errors="coerce"),
        })
        out = out.dropna(subset=["date", "port", "value"])
        if not out.empty:
            return out.groupby(["date", "port"], as_index=False).value.sum().sort_values(["date", "port"])

    # Handle a wide table: one date column and one numeric column per port.
    best_date_col = None
    best_count = 0
    for c in df.columns:
        parsed = _parse_date(df[c])
        if parsed.notna().sum() > best_count:
            best_date_col, best_count = c, parsed.notna().sum()
    if best_date_col and best_count >= 3:
        work = df.copy()
        work["date"] = _parse_date(work[best_date_col])
        port_cols = [c for c in work.columns if c not in {best_date_col, "date"} and _clean_port(c) in set(PORT_ALIASES.values())]
        if port_cols:
            out = work[["date"] + port_cols].melt("date", var_name="port", value_name="value")
            out["port"] = out["port"].map(_clean_port)
            out["value"] = pd.to_numeric(out["value"].astype(str).str.replace(",", "", regex=False), errors="coerce")
            return out.dropna(subset=["date", "port", "value"]).sort_values(["date", "port"])
    raise ValueError(f"Current BTS CSV schema was not recognized. Columns: {list(df.columns)}")

def _current_tableau() -> pd.DataFrame:
    errors = []
    headers = {"User-Agent": "Mozilla/5.0 CRE-leading-indicators/1.0"}
    for url in TABLEAU_CSV_URLS:
        try:
            response = requests.get(url, headers=headers, timeout=60)
            response.raise_for_status()
            out = _parse_tableau_csv(response.content)
            if not out.empty:
                return out
        except Exception as exc:
            errors.append(str(exc))
    raise RuntimeError("; ".join(errors) or "BTS Tableau CSV returned no data")

def _legacy_socrata() -> pd.DataFrame:
    response = requests.get(LEGACY_SOCRATA_URL, timeout=45)
    response.raise_for_status()
    raw = pd.DataFrame(response.json())
    if raw.empty:
        return pd.DataFrame(columns=["date", "port", "value"])
    date_col = "port" if "port" in raw.columns else raw.columns[0]
    raw["date"] = _parse_date(raw[date_col])
    cols = [c for c in LEGACY_PORT_COLUMNS if c in raw.columns]
    out = raw[["date"] + cols].melt("date", var_name="port_key", value_name="value")
    out["port"] = out.port_key.map(LEGACY_PORT_COLUMNS)
    out["value"] = pd.to_numeric(out.value, errors="coerce")
    return out.dropna(subset=["date", "port", "value"])[["date", "port", "value"]].sort_values(["date", "port"])

def monthly_teu() -> pd.DataFrame:
    """Return current BTS monthly TEUs in long format: date, port, value.

    The current Tableau-backed BTS dataset is attempted first. The old Socrata
    dataset is used only if the current feed is unavailable, and the returned
    frame records its source in attrs so the UI can warn that it is stale.
    """
    try:
        out = _current_tableau()
        out.attrs["feed"] = "BTS current Monthly TEU dashboard"
        out.attrs["stale_fallback"] = False
        return out
    except Exception as current_error:
        out = _legacy_socrata()
        out.attrs["feed"] = "BTS legacy 2022 Socrata dataset"
        out.attrs["stale_fallback"] = True
        out.attrs["current_feed_error"] = str(current_error)
        return out
