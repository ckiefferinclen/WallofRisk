from __future__ import annotations
import os, json, requests, pandas as pd
from pathlib import Path

EXPORT = Path("data/rca_export.csv")

def load_export(series: str) -> pd.DataFrame:
    if not EXPORT.exists(): return pd.DataFrame(columns=["date","value"])
    df = pd.read_csv(EXPORT)
    required={"series","date","value"}
    if not required.issubset(df.columns):
        raise ValueError("data/rca_export.csv must contain series,date,value columns")
    df=df[df["series"]==series].copy()
    df["date"]=pd.to_datetime(df["date"],errors="coerce")
    df["value"]=pd.to_numeric(df["value"],errors="coerce")
    return df.dropna(subset=["date","value"]).sort_values("date")[["date","value"]]

def load_api(series: str, secrets: dict) -> pd.DataFrame:
    # Generic adapter because MSCI has not publicly documented the RCA endpoint/schema.
    # Set endpoint_map JSON in Streamlit secrets after MSCI provides the exact endpoint.
    cfg=secrets.get("msci",{}) if secrets else {}
    base=cfg.get("base_url"); endpoint_map=cfg.get("endpoint_map",{})
    endpoint=endpoint_map.get(series) if isinstance(endpoint_map,dict) else None
    if not base or not endpoint: return pd.DataFrame(columns=["date","value"])
    headers={"Accept":"application/json"}
    token=cfg.get("bearer_token")
    if token: headers["Authorization"]=f"Bearer {token}"
    r=requests.get(base.rstrip("/")+"/"+endpoint.lstrip("/"),headers=headers,timeout=45)
    r.raise_for_status(); payload=r.json()
    path_map=cfg.get("records_path",{})
    records=payload
    for key in path_map.get(series,[]): records=records[key]
    df=pd.DataFrame(records)
    cols=cfg.get("field_map",{}).get(series,{"date":"date","value":"value"})
    df=df.rename(columns={cols["date"]:"date",cols["value"]:"value"})
    df["date"]=pd.to_datetime(df["date"],errors="coerce")
    df["value"]=pd.to_numeric(df["value"],errors="coerce")
    return df.dropna(subset=["date","value"]).sort_values("date")[["date","value"]]
