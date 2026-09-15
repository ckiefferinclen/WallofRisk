from __future__ import annotations
from datetime import date
import requests
import pandas as pd

BASE = "https://api.census.gov/data/timeseries/intltrade/imports/porths"
FIELDS = "PORT,PORT_NAME,CNT_VAL_MO,CNT_WGT_MO,VES_WGT_MO,GEN_VAL_MO,LAST_UPDATE"
HEADERS = {"User-Agent": "cre-leading-indicators/3.0"}

def _periods(months=48):
    y, m = date.today().year, date.today().month
    for offset in range(months):
        n = m - offset
        yield f"{y + (n-1)//12:04d}-{(n-1)%12+1:02d}"

def _fetch(period, api_key=None):
    attempts = [
        {"time": period, "I_COMMODITY": "TOTAL", "CTY_CODE": "-"},
        {"time": period, "I_COMMODITY": "TOTAL", "CTY_CODE": "0"},
        {"time": period, "I_COMMODITY": "TOTAL"},
    ]
    errors=[]
    for extra in attempts:
        params={"get":FIELDS, **extra}
        if api_key: params["key"]=api_key
        try:
            r=requests.get(BASE,params=params,headers=HEADERS,timeout=45)
            r.raise_for_status(); payload=r.json()
            if isinstance(payload,list) and len(payload)>1:
                return pd.DataFrame(payload[1:],columns=payload[0])
        except Exception as exc: errors.append(str(exc))
    raise RuntimeError(f"No Census port rows for {period}: {' | '.join(errors[:2])}")

def monthly_port_trade(api_key=None, months=48):
    frames=[]; consecutive_misses=0
    for period in _periods(months):
        try:
            x=_fetch(period,api_key); x["date"]=pd.Timestamp(period+"-01"); frames.append(x); consecutive_misses=0
        except Exception:
            consecutive_misses += 1
            if frames and consecutive_misses>=4: break
    if not frames: raise RuntimeError("Census International Trade API returned no monthly port data")
    x=pd.concat(frames,ignore_index=True)
    x["port"]=x["PORT_NAME"].astype(str).str.strip()
    for old,new in [("CNT_VAL_MO","container_value"),("CNT_WGT_MO","container_weight"),("VES_WGT_MO","vessel_weight"),("GEN_VAL_MO","general_import_value")]:
        x[new]=pd.to_numeric(x.get(old),errors="coerce")
    x=x.dropna(subset=["date","port","container_value"])
    out=x.groupby(["date","port"],as_index=False).agg(container_value=("container_value","sum"),container_weight=("container_weight","sum"),vessel_weight=("vessel_weight","sum"),general_import_value=("general_import_value","sum"))
    out.attrs["feed"]="U.S. Census International Trade API"
    return out.sort_values(["date","port"])
