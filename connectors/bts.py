from __future__ import annotations
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
import requests
import pandas as pd

BASE = "https://api.census.gov/data/timeseries/intltrade/imports/porths"
FIELDS = "PORT,PORT_NAME,CNT_VAL_MO,CNT_WGT_MO,VES_WGT_MO,GEN_VAL_MO,LAST_UPDATE"
HEADERS = {"User-Agent": "cre-leading-indicators/7.0"}

# Census Schedule D ports grouped into economically comparable gateway markets.
# 2704 is already the combined Los Angeles/Long Beach Seaport, so 2709 is
# deliberately excluded to prevent double counting Long Beach.
GATEWAYS = {
    "Los Angeles / Long Beach": ["2704"],
    "New York / New Jersey": ["1001", "1003", "1004"],
    "Seattle / Tacoma": ["3001", "3002"],
    "Norfolk / Virginia": ["1401"],
    "Charleston": ["1601"],
    "Savannah": ["1703"],
    "Oakland": ["2811"],
    "Houston": ["5301"],
}
PORT_TO_GATEWAY = {code: gateway for gateway, codes in GATEWAYS.items() for code in codes}


def _periods(months: int = 15):
    y, m = date.today().year, date.today().month
    for offset in range(months):
        n = m - offset
        yield f"{y + (n - 1) // 12:04d}-{(n - 1) % 12 + 1:02d}"


def _fetch(period: str, port_code: str, api_key: str) -> pd.DataFrame:
    params = {"get": FIELDS, "time": period, "PORT": port_code, "key": api_key}
    response = requests.get(BASE, params=params, headers=HEADERS, timeout=12)
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, list) or len(payload) < 2:
        return pd.DataFrame()
    frame = pd.DataFrame(payload[1:], columns=payload[0])
    frame["date"] = pd.Timestamp(period + "-01")
    frame["port_code"] = port_code
    frame["port"] = PORT_TO_GATEWAY[port_code]
    return frame


def monthly_port_trade(api_key: str | None = None, months: int = 15) -> pd.DataFrame:
    """Return recent monthly trade activity grouped by major port gateway."""
    if not api_key:
        raise RuntimeError("CENSUS_API_KEY is required")

    jobs = [(period, code) for period in _periods(months) for code in PORT_TO_GATEWAY]
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
    # This is where Newark and Perth Amboy are combined with New York, and
    # Seattle is combined with Tacoma. LA/LB remains a single 2704 gateway.
    out = raw.groupby(["date", "port"], as_index=False).agg(
        container_value=("container_value", "sum"),
        container_weight=("container_weight", "sum"),
        vessel_weight=("vessel_weight", "sum"),
        general_import_value=("general_import_value", "sum"),
    )
    if out.empty:
        raise RuntimeError("Census returned rows but no numeric gateway measures")
    out.attrs["feed"] = "U.S. Census International Trade API"
    out.attrs["partial_errors"] = len(errors)
    out.attrs["gateway_method"] = "Schedule D ports grouped into gateway markets"
    return out.sort_values(["date", "port"])
