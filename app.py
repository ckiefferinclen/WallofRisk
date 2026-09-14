from __future__ import annotations
from pathlib import Path
import math, yaml, pandas as pd, numpy as np, streamlit as st
import plotly.graph_objects as go
from connectors.public_data import fred_series
from connectors.rca import load_export, load_api

st.set_page_config(page_title="CRE Leading Indicators", page_icon="🏢", layout="wide")
PAGES=["CRE Macro Signal Board","Industrial Demand","Office Demand","Risk Monitor","Methodology & Sources"]
COLORS={"Supportive":"#2E7D32","Neutral":"#F9A825","Restrictive":"#C62828","No data":"#7A869A"}

@st.cache_data(ttl=21600,show_spinner=False)
def config():
    return yaml.safe_load(Path("config/metrics.yml").read_text())["metrics"]

def local_export(series):
    p=Path("data/manual_export.csv")
    if not p.exists(): return pd.DataFrame(columns=["date","value"])
    try: df=pd.read_csv(p,comment="#")
    except Exception: return pd.DataFrame(columns=["date","value"])
    if not {"series","date","value"}.issubset(df.columns): return pd.DataFrame(columns=["date","value"])
    df=df[df.series==series].copy(); df["date"]=pd.to_datetime(df.date,errors="coerce"); df["value"]=pd.to_numeric(df.value,errors="coerce")
    return df.dropna().sort_values("date")[["date","value"]]

@st.cache_data(ttl=21600,show_spinner=False)
def raw_source(source,series):
    try:
        if source=="fred": return fred_series(series)
        if source=="export": return local_export(series)
        if source=="rca":
            try:
                api=load_api(series,dict(st.secrets))
                if not api.empty: return api
            except Exception: pass
            return load_export(series)
    except Exception: pass
    return pd.DataFrame(columns=["date","value"])

def derived(series,loaded):
    def d(name): return loaded.get(name,pd.DataFrame(columns=["date","value"]))
    if series=="mortgage_spread":
        a,b=d("commercial_mortgage_rate"),d("DGS10")
        if a.empty or b.empty:return d("")
        x=pd.merge_asof(a.sort_values("date"),b.sort_values("date"),on="date",direction="backward",suffixes=("_a","_b")); return x.assign(value=(x.value_a-x.value_b)*100)[["date","value"]]
    if series=="cap_treasury_spread":
        a=d("industrial_cap_rate"); b=d("DGS10")
        if a.empty or b.empty:return d("")
        x=pd.merge_asof(a.sort_values("date"),b.sort_values("date"),on="date",direction="backward",suffixes=("_a","_b")); return x.assign(value=(x.value_a-x.value_b)*100)[["date","value"]]
    if series=="office_employment":
        parts=[d("USINFO"),d("USFIRE"),d("USPBS")]
        if any(x.empty for x in parts): return d("")
        x=parts[0].rename(columns={"value":"v0"})
        for i,p in enumerate(parts[1:],1): x=pd.merge_asof(x,p.rename(columns={"value":f"v{i}"}),on="date",direction="nearest",tolerance=pd.Timedelta("10d"))
        x["value"]=x[["v0","v1","v2"]].sum(axis=1,min_count=3); return x[["date","value"]].dropna()
    if series=="energy_shock":
        x=d("GASDESW").copy()
        if x.empty:return x
        x["ret"]=x.value.pct_change(); x["value"]=(x.ret-x.ret.rolling(52).mean())/x.ret.rolling(52).std(); return x[["date","value"]].dropna()
    return d("")

def point(df,months=0):
    if df.empty:return np.nan
    cutoff=df.date.max()-pd.DateOffset(months=months)
    z=df[df.date<=cutoff]
    return z.iloc[-1].value if not z.empty else np.nan

def signal(m,df):
    if df.empty:return "No data",0
    cur=point(df); yoy=cur/point(df,12)-1 if point(df,12) not in (0,np.nan) and pd.notna(point(df,12)) else np.nan
    rule=m["rule"]; w=m.get("warn",0); d=m.get("danger",0)
    if rule=="balanced": return "Neutral",0
    value=yoy if rule.startswith("momentum") else cur
    if pd.isna(value): return "No data",0
    supportive = rule in ("higher_supportive","momentum_higher")
    if supportive:
        lab="Supportive" if value>=w else ("Restrictive" if value<=d else "Neutral")
    else:
        lab="Supportive" if value<=w else ("Restrictive" if value>=d else "Neutral")
    return lab,{"Supportive":1,"Neutral":0,"Restrictive":-1}[lab]

def build_all():
    ms=config(); loaded={}
    # Additional series required for derived office employment
    for s in ["USINFO","USFIRE","USPBS"]:
        loaded[s]=raw_source("fred",s)
    for m in ms:
        if m["source"] in ["fred","rca","export"]: loaded[m["series"]]=raw_source(m["source"],m["series"])
    for m in ms:
        if m["source"]=="derived" and m["series"]!="composite": loaded[m["series"]]=derived(m["series"],loaded)
    scores=[]
    for m in ms:
        if m["series"]!="composite":
            lab,score=signal(m,loaded.get(m["series"],pd.DataFrame()))
            if lab!="No data" and m.get("weight",0)>0:scores.append((score,m["weight"]))
    composite=sum(s*w for s,w in scores)/sum(w for _,w in scores) if scores else np.nan
    loaded["composite"]=pd.DataFrame({"date":[pd.Timestamp.today().normalize()],"value":[composite]}) if pd.notna(composite) else pd.DataFrame(columns=["date","value"])
    return ms,loaded

def fmt(v,unit):
    if pd.isna(v):return "Waiting for data"
    if unit=="%":return f"{v:.2f}%"
    if unit=="bp":return f"{v:,.0f} bp"
    if unit=="$/gal":return f"${v:.2f}"
    if unit in ("$bn","$mm"):return f"${v:,.1f}"
    if unit=="score":return f"{v:+.2f}"
    return f"{v:,.2f}"

def tile(m,df):
    lab,score=signal(m,df)
    if m["series"]=="composite" and not df.empty:
        v=df.iloc[-1].value; lab="Supportive" if v>=.25 else ("Restrictive" if v<=-.25 else "Neutral")
    cur=point(df); prior=point(df,1); three=point(df,3); year=point(df,12)
    direction="—" if pd.isna(cur) or pd.isna(three) else ("↑" if cur>three else "↓" if cur<three else "→")
    yoy=np.nan if pd.isna(cur) or pd.isna(year) or year==0 else cur/year-1
    date="No observation" if df.empty else df.date.max().strftime("%b %d, %Y")
    st.markdown(f"<div style='background:white;border-radius:12px;padding:14px;border-top:5px solid {COLORS[lab]};box-shadow:0 2px 8px #00000012'><div style='font-weight:700;height:42px'>{m['name']}</div><div style='font-size:27px;font-weight:800'>{fmt(cur,m['unit'])}</div><div style='color:{COLORS[lab]};font-weight:700'>{lab}</div><div style='font-size:12px;color:#58677A;margin-top:8px'>Prior: {fmt(prior,m['unit'])} &nbsp; | &nbsp; 3-mo {direction}<br>YoY: {'—' if pd.isna(yoy) else f'{yoy:+.1%}'} &nbsp; | &nbsp; {date}</div></div>",unsafe_allow_html=True)
    if not df.empty:
        z=df.tail(60); fig=go.Figure(go.Scatter(x=z.date,y=z.value,mode="lines",line=dict(color=COLORS[lab],width=2)))
        fig.update_layout(height=90,margin=dict(l=0,r=0,t=2,b=0),showlegend=False,xaxis=dict(visible=False),yaxis=dict(visible=False),paper_bgcolor="rgba(0,0,0,0)",plot_bgcolor="rgba(0,0,0,0)")
        st.plotly_chart(fig,use_container_width=True,config={"displayModeBar":False},key=m["series"])

st.sidebar.title("CRE Dashboard")
page=st.sidebar.radio("View",PAGES)
if st.sidebar.button("Refresh data",use_container_width=True): st.cache_data.clear(); st.rerun()
ms,loaded=build_all()
st.title(page)
st.caption("Signals describe whether conditions are supportive, neutral, or restrictive for CRE investment conditions. Direction alone is not treated as good or bad.")
if page=="Methodology & Sources":
    st.subheader("Signal methodology")
    st.write("Level rules compare the latest observation with configurable thresholds. Momentum rules compare the latest reading with the prior-year reading. Balanced metrics remain neutral until an investment-specific rule is approved. The composite is the weighted average of available signals, where Supportive = +1, Neutral = 0, and Restrictive = -1.")
    st.subheader("Data coverage")
    rows=[]
    for m in ms:
        df=loaded.get(m["series"],pd.DataFrame()); rows.append({"Metric":m["name"],"Page":m["page"],"Source":m["source"].upper(),"Series":m["series"],"Latest observation":None if df.empty else df.date.max().date(),"Status":"Connected" if not df.empty else "Awaiting feed"})
    st.dataframe(pd.DataFrame(rows),use_container_width=True,hide_index=True)
    st.info("RCA/MSCI metrics first attempt the optional API adapter, then fall back to data/rca_export.csv. No MSCI credentials are stored in the repository.")
else:
    chosen=[m for m in ms if m["page"]==page]
    cols=st.columns(3)
    for i,m in enumerate(chosen):
        with cols[i%3]: tile(m,loaded.get(m["series"],pd.DataFrame()))
