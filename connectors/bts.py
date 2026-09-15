from __future__ import annotations
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
import requests
import pandas as pd

# Stable Census International Trade "imports by port" endpoint.
BASE = "https://api.census.gov/data/timeseries/intltrade/imports/porths"
FIELDS = "PORT,PORT_NAME,CNT_VAL_MO,CNT_WGT_MO,VES_WGT_MO,GEN_VAL_MO,LAST_UPDATE"
HEADERS = {"User-Agent": "cre-leading-indicators/6.0"}

# Major U.S. container gateways and their Census customs port codes.
PORTS = {
    "1001": "New York, NY",
    "1401": "Norfolk, VA",
    "1601": "Charleston, SC",
    "1703": "Savannah, GA",
    "2704": "Los Angeles, CA",
    "2709": "Long Beach, CA",
    "2811": "Oakland, CA",
    "3001": "Seattle, WA",
    "3002": "Tacoma, WA",
    "5301": "Houston, TX",
}


def _periods(months: int = 15):
    y, m = date.today().year, date.today().month
    for offset in range(months):
        n = m - offset
        yield f"{y + (n - 1) // 12:04d}-{(n - 1) % 12 + 1:02d}"


def _fetch(period: str, port_code: str, api_key: str) -> pd.DataFrame:
    params = {
        "get": FIELDS,
        "time": period,
        "PORT": port_code,
        "key": api_key,
    }
    response = requests.get(BASE, params=params, headers=HEADERS, timeout=12)
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, list) or len(payload) < 2:
        return pd.DataFrame()
    frame = pd.DataFrame(payload[1:], columns=payload[0])
    frame["date"] = pd.Timestamp(period + "-01")
    frame["port"] = PORTS[port_code]
    return frame


def monthly_port_trade(api_key: str | None = None, months: int = 15) -> pd.DataFrame:
    """Return 15 recent months for ten major container gateways.

    The bounded request set supplies current, prior-month, three-month and
    year-over-year dashboard comparisons without blocking Streamlit startup.
    """
    if not api_key:
        raise RuntimeError("CENSUS_API_KEY is required")

    jobs = [(period, code) for period in _periods(months) for code in PORTS]
    frames, errors = [], []
    with ThreadPoolExecutor(max_workers=20) as pool:
        futures = {pool.submit(_fetch, period, code, api_key): (period, code) for period, code in jobs}
        for future in as_completed(futures):
            period, code = futures[future]
            try:
                frame = future.result()
                if not frame.empty:
                    frames.append(frame)
            except Exception as exc:
                errors.append(f"{period}/{code}: {exc}")

    if not frames:
        sample = errors[0] if errors else "no rows returned"
        raise RuntimeError(f"Census port feed returned no data: {sample}")

    raw = pd.concat(frames, ignore_index=True).drop_duplicates()
    for source, target in {
        "CNT_VAL_MO": "container_value",
        "CNT_WGT_MO": "container_weight",
        "VES_WGT_MO": "vessel_weight",
        "GEN_VAL_MO": "general_import_value",
    }.items():
        raw[target] = pd.to_numeric(raw.get(source), errors="coerce")

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
