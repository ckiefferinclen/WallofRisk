from __future__ import annotations
import requests, pandas as pd

BTS_RESOURCE_URL = "https://data.bts.gov/resource/rd72-aq8r.json?$limit=5000"
PORT_LABELS = {
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

def monthly_teu() -> pd.DataFrame:
    """Return BTS monthly TEUs in long format: date, port, value."""
    response = requests.get(BTS_RESOURCE_URL, timeout=45)
    response.raise_for_status()
    raw = pd.DataFrame(response.json())
    if raw.empty:
        return pd.DataFrame(columns=["date", "port", "value"])

    # BTS currently exposes a wide table. The period field has appeared as
    # 'port' in the dataset metadata, so detect date-like values defensively.
    date_col = None
    for col in raw.columns:
        parsed = pd.to_datetime(raw[col], errors="coerce")
        if parsed.notna().sum() >= max(3, len(raw) // 2):
            date_col = col
            raw["date"] = parsed
            break
    if date_col is None:
        raise ValueError("BTS TEU feed did not contain a recognizable monthly date field")

    available = [c for c in PORT_LABELS if c in raw.columns]
    if not available:
        raise ValueError("BTS TEU feed did not contain expected port columns")
    long = raw[["date"] + available].melt("date", var_name="port_key", value_name="value")
    long["value"] = pd.to_numeric(long["value"], errors="coerce")
    long["port"] = long["port_key"].map(PORT_LABELS)
    return long.dropna(subset=["date", "value"]).sort_values(["date", "port"])[["date", "port", "value"]]
