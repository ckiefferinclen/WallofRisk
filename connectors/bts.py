from __future__ import annotations
from datetime import date
import requests
import pandas as pd

# Geography-enabled Census import-by-port endpoint.
BASE = "https://api.census.gov/data/timeseries/intltrade/imports/porthsimport"
FIELDS = "NAME,GEN_VAL_MO,CNT_VAL_MO,CNT_WGT_MO,VES_WGT_MO,YEAR,MONTH"
HEADERS = {"User-Agent": "cre-leading-indicators/4.0"}


def _periods(months: int = 48):
    y, m = date.today().year, date.today().month
    for offset in range(months):
        n = m - offset
        yield f"{y + (n - 1) // 12:04d}-{(n - 1) % 12 + 1:02d}"


def _query(period: str, api_key: str) -> pd.DataFrame:
    if not api_key:
        raise RuntimeError("CENSUS_API_KEY is required by the Census International Trade API")

    # First try all customs districts in one geography request. If the API
    # rejects the wildcard parent geography, fall back to individual districts.
    parent_scopes = ["customs district:*"] + [f"customs district:{i:02d}" for i in range(1, 56)]
    frames = []
    for scope in parent_scopes:
        params = {
            "get": FIELDS,
            "for": "port:*",
            "in": scope,
            "time": period,
            "I_COMMODITY": "TOTAL",
            "key": api_key,
        }
        try:
            response = requests.get(BASE, params=params, headers=HEADERS, timeout=45)
            response.raise_for_status()
            payload = response.json()
            if isinstance(payload, list) and len(payload) > 1:
                frame = pd.DataFrame(payload[1:], columns=payload[0])
                frames.append(frame)
                if scope == "customs district:*":
                    break
        except Exception:
            if scope == "customs district:*":
                continue
    if not frames:
        raise RuntimeError(f"No Census port rows returned for {period}")
    return pd.concat(frames, ignore_index=True).drop_duplicates()


def monthly_port_trade(api_key: str | None = None, months: int = 48) -> pd.DataFrame:
    """Return monthly U.S. import activity by port from the Census Trade API."""
    frames = []
    misses = 0
    for period in _periods(months):
        try:
            frame = _query(period, api_key or "")
            frame["date"] = pd.Timestamp(period + "-01")
            frames.append(frame)
            misses = 0
        except Exception:
            misses += 1
            if frames and misses >= 4:
                break
    if not frames:
        raise RuntimeError("Census port feed returned no data. Verify CENSUS_API_KEY is active in Streamlit secrets.")

    raw = pd.concat(frames, ignore_index=True)
    raw["port"] = raw["NAME"].astype(str).str.strip()
    mapping = {
        "CNT_VAL_MO": "container_value",
        "CNT_WGT_MO": "container_weight",
        "VES_WGT_MO": "vessel_weight",
        "GEN_VAL_MO": "general_import_value",
    }
    for source, target in mapping.items():
        raw[target] = pd.to_numeric(raw[source], errors="coerce")

    raw = raw.dropna(subset=["date", "port", "container_value"])
    out = raw.groupby(["date", "port"], as_index=False).agg(
        container_value=("container_value", "sum"),
        container_weight=("container_weight", "sum"),
        vessel_weight=("vessel_weight", "sum"),
        general_import_value=("general_import_value", "sum"),
    )
    out.attrs["feed"] = "U.S. Census International Trade API"
    return out.sort_values(["date", "port"])
