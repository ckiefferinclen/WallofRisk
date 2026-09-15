from __future__ import annotations
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
import requests
import pandas as pd

BASE = "https://api.census.gov/data/timeseries/intltrade/imports/porthsimport"
FIELDS = "NAME,GEN_VAL_MO,CNT_VAL_MO,CNT_WGT_MO,VES_WGT_MO,YEAR,MONTH"
HEADERS = {"User-Agent": "cre-leading-indicators/5.0"}

# Customs districts containing the major U.S. container gateways. Limiting the
# query to these districts avoids thousands of serial API calls.
DISTRICTS = {
    "10": "New York",
    "13": "Baltimore",
    "14": "Norfolk",
    "16": "Charleston",
    "17": "Savannah",
    "27": "Los Angeles",
    "28": "San Francisco",
    "30": "Seattle",
    "53": "Houston",
    "18": "Tampa",
    "52": "Miami",
}


def _periods(months: int = 30):
    y, m = date.today().year, date.today().month
    for offset in range(months):
        n = m - offset
        yield f"{y + (n - 1) // 12:04d}-{(n - 1) % 12 + 1:02d}"


def _fetch_one(period: str, district: str, api_key: str) -> pd.DataFrame:
    params = {
        "get": FIELDS,
        "for": "port:*",
        "in": f"customs district:{district}",
        "time": period,
        "I_COMMODITY": "TOTAL",
        "key": api_key,
    }
    response = requests.get(BASE, params=params, headers=HEADERS, timeout=25)
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, list) or len(payload) < 2:
        return pd.DataFrame()
    frame = pd.DataFrame(payload[1:], columns=payload[0])
    frame["date"] = pd.Timestamp(period + "-01")
    return frame


def monthly_port_trade(api_key: str | None = None, months: int = 30) -> pd.DataFrame:
    """Return recent monthly trade activity for major U.S. container ports.

    Requests are limited to major gateway customs districts and run concurrently.
    This keeps a Streamlit refresh bounded instead of making thousands of serial
    calls across every customs district and month.
    """
    if not api_key:
        raise RuntimeError("CENSUS_API_KEY is required")

    jobs = [(period, district) for period in _periods(months) for district in DISTRICTS]
    frames, errors = [], []
    with ThreadPoolExecutor(max_workers=12) as pool:
        futures = {pool.submit(_fetch_one, period, district, api_key): (period, district) for period, district in jobs}
        for future in as_completed(futures):
            period, district = futures[future]
            try:
                frame = future.result()
                if not frame.empty:
                    frames.append(frame)
            except Exception as exc:
                errors.append(f"{period}/{district}: {exc}")

    if not frames:
        detail = errors[0] if errors else "no rows returned"
        raise RuntimeError(f"Census port feed returned no data: {detail}")

    raw = pd.concat(frames, ignore_index=True).drop_duplicates()
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
    if out.empty:
        raise RuntimeError("Census returned rows but no numeric port measures")
    out.attrs["feed"] = "U.S. Census International Trade API"
    out.attrs["partial_errors"] = len(errors)
    return out.sort_values(["date", "port"])
