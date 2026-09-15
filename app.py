from __future__ import annotations
from pathlib import Path
import yaml, pandas as pd, numpy as np, streamlit as st
import plotly.graph_objects as go
from connectors.public_data import fred_series
from connectors.rca import load_export, load_api

st.set_page_config(page_title="CRE Leading Indicators", page_icon="🏢", layout="wide")
PAGES=["CRE Macro Signal Board","Industrial Demand","Office Demand","Risk Monitor","Methodology & Sources"]
COLORS={"Supportive":"#2E7D32","Neutral":"#F9A825","Restrictive":"#C62828","No data":"#7A869A"}
SOURCE_LABELS={"fred":"FRED","rca":"MSCI / RCA","derived":"Calculated","export":"Manual export file"}

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

def empty_df(): return pd.DataFrame(columns=["date","value"])

def derived(series,loaded):
    def d(name): return loaded.get(name,empty_df())
    if series=="mortgage_spread":
        a,b=d("commercial_mortgage_rate"),d("DGS10")
        if a.empty or b.empty:return empty_df()
        x=pd.merge_asof(a.sort_values("date"),b.sort_values("date"),on="date",direction="backward",suffixes=("_a","_b")); return x.assign(value=(x.value_a-x.value_b)*100)[["date","value"]]
    if series=="cap_treasury_spread":
        a,b=d("industrial_cap_rate"),d("DGS10")
        if a.empty or b.empty:return empty_df()
        x=pd.merge_asof(a.sort_values("date"),b.sort_values("date"),on="date",direction="backward",suffixes=("_a","_b")); return x.assign(value=(x.value_a-x.value_b)*100)[["date","value"]]
    if series=="office_employment":
        parts=[d("USINFO"),d("USFIRE"),d("USPBS")]
        if any(x.empty for x in parts): return empty_df()
        x=parts[0].rename(columns={"value":"v0"})
        for i,p in enumerate(parts[1:],1): x=pd.merge_asof(x,p.rename(columns={"value":f"v{i}"}),on="date",direction="nearest",tolerance=pd.Timedelta("10d"))
        x["value"]=x[["v0","v1","v2"]].sum(axis=1,min_count=3); return x[["date","value"]].dropna()
    if series=="energy_shock":
        x=d("GASDESW").copy()
        if x.empty:return x
        x["ret"]=x.value.pct_change(); x["value"]=(x.ret-x.ret.rolling(52).mean())/x.ret.rolling(52).std(); return x[["date","value"]].dropna()
    return empty_df()

def point(df,months=0):
    if df.empty:return np.nan
    cutoff=df.date.max()-pd.DateOffset(months=months)
    z=df[df.date<=cutoff]
    return z.iloc[-1].value if not z.empty else np.nan

def effective_thresholds(m):
    key=m["series"]
    return st.session_state.get(f"warn_{key}",float(m.get("warn",0))),st.session_state.get(f"danger_{key}",float(m.get("danger",0)))

def signal(m,df):
    if df.empty:return "No data",0
    cur=point(df); prior_year=point(df,12)
    yoy=cur/prior_year-1 if pd.notna(prior_year) and prior_year!=0 else np.nan
    rule=m["rule"]; w,d=effective_thresholds(m)
    if rule=="balanced": return "Neutral",0
    if rule=="composite":
        if pd.isna(cur): return "No data",0
        return ("Supportive",1) if cur>=0.25 else (("Restrictive",-1) if cur<=-0.25 else ("Neutral",0))
    value=yoy if rule.startswith("momentum") else cur
    if pd.isna(value): return "No data",0
    supportive=rule in ("higher_supportive","momentum_higher")
    if supportive: lab="Supportive" if value>=w else ("Restrictive" if value<=d else "Neutral")
    else: lab="Supportive" if value<=w else ("Restrictive" if value>=d else "Neutral")
    return lab,{"Supportive":1,"Neutral":0,"Restrictive":-1}[lab]

def hurdle_text(m):
    rule=m["rule"]; w,d=effective_thresholds(m); unit=m["unit"]
    if rule=="balanced": return "Rating rule: Neutral pending an approved investment-specific hurdle."
    if rule=="composite": return "Supportive ≥ +0.25 | Neutral between -0.25 and +0.25 | Restrictive ≤ -0.25"
    def show(v):
        if rule.startswith("momentum"): return f"{v:+.1%} YoY"
        if unit=="%": return f"{v:.2f}%"
        if unit=="bp": return f"{v:.0f} bp"
        return f"{v:g}"
    if rule in ("higher_supportive","momentum_higher"):
        return f"Supportive ≥ {show(w)} | Neutral between | Restrictive ≤ {show(d)}"
    return f"Supportive ≤ {show(w)} | Neutral between | Restrictive ≥ {show(d)}"

def build_all():
    ms=config(); loaded={}
    for s in ["USINFO","USFIRE","USPBS"]: loaded[s]=raw_source("fred",s)
    for m in ms:
        if m["source"] in ["fred","rca","export"]: loaded[m["series"]]=raw_source(m["source"],m["series"])
    for m in ms:
        if m["source"]=="derived" and m["series"]!="composite": loaded[m["series"]]=derived(m["series"],loaded)
    scores=[]
    for m in ms:
        if m["series"]!="composite":
            lab,score=signal(m,loaded.get(m["series"],empty_df()))
            if lab!="No data" and m.get("weight",0)>0:scores.append((score,m["weight"]))
    composite=sum(s*w for s,w in scores)/sum(w for _,w in scores) if scores else np.nan
    loaded["composite"]=pd.DataFrame({"date":[pd.Timestamp.today().normalize()],"value":[composite]}) if pd.notna(composite) else empty_df()
    return ms,loaded

def fmt(v,unit):
    if pd.isna(v):return "Waiting for data"
    if unit=="%":return f"{v:.2f}%"
    if unit=="bp":return f"{v:,.0f} bp"
    if unit=="$/gal":return f"${v:.2f}"
    if unit in ("$bn","$mm"):return f"${v:,.1f}"
    if unit=="score":return f"{v:+.2f}"
    return f"{v:,.2f}"

def source_text(m):
    base=SOURCE_LABELS.get(m["source"],m["source"].upper())
    return f"Source: {base}" + (f" · {m['series']}" if m["source"]=="fred" else "")

def tile(m,df):
    lab,_=signal(m,df); cur=point(df); prior=point(df,1); three=point(df,3); year=point(df,12)
    direction="—" if pd.isna(cur) or pd.isna(three) else ("↑" if cur>three else "↓" if cur<three else "→")
    yoy=np.nan if pd.isna(cur) or pd.isna(year) or year==0 else cur/year-1
    date="No observation" if df.empty else df.date.max().strftime("%b %d, %Y")
    with st.container(border=True):
        st.markdown(f"<div style='border-top:5px solid {COLORS[lab]};padding-top:8px'><div style='font-size:1.05rem;font-weight:750;line-height:1.2'>{m['name']}</div><div style='font-size:.76rem;color:#6B778C;margin-top:3px'>{source_text(m)}</div><div style='font-size:1.85rem;font-weight:800;margin-top:8px'>{fmt(cur,m['unit'])}</div><div style='color:{COLORS[lab]};font-weight:800;font-size:1rem'>{lab}</div></div>",unsafe_allow_html=True)
        c1,c2,c3=st.columns(3)
        c1.markdown(f"<div style='font-size:.9rem'><b>Prior</b><br>{fmt(prior,m['unit'])}</div>",unsafe_allow_html=True)
        c2.markdown(f"<div style='font-size:.9rem'><b>3-mo trend</b><br>{direction}</div>",unsafe_allow_html=True)
        c3.markdown(f"<div style='font-size:.9rem'><b>YoY</b><br>{'—' if pd.isna(yoy) else f'{yoy:+.1%}'}</div>",unsafe_allow_html=True)
        st.caption(f"Latest observation: {date}")
        if not df.empty:
            z=df.tail(60)
            fig=go.Figure(go.Scatter(x=z.date,y=z.value,mode="lines",line=dict(color=COLORS[lab],width=2)))
            fig.update_layout(height=185,margin=dict(l=8,r=8,t=8,b=8),showlegend=False,paper_bgcolor="rgba(0,0,0,0)",plot_bgcolor="#FAFBFC",font=dict(size=10))
            fig.update_xaxes(showgrid=True,gridcolor="#D9DEE7",tickformat="%b\n%Y",nticks=5,showline=True,linecolor="#AAB3C2")
            fig.update_yaxes(showgrid=True,gridcolor="#D9DEE7",nticks=5,showline=True,linecolor="#AAB3C2")
            st.plotly_chart(fig,use_container_width=True,config={"displayModeBar":False},key=f"chart_{m['series']}")
        else:
            st.info("Awaiting source data")
        st.markdown(f"<div style='font-size:.72rem;color:#667085;line-height:1.3'>{hurdle_text(m)}</div>",unsafe_allow_html=True)

st.sidebar.title("CRE Dashboard")
page=st.sidebar.radio("View",PAGES)
if st.sidebar.button("Refresh data",use_container_width=True): st.cache_data.clear(); st.rerun()
ms=config()
with st.sidebar.expander("Adjust signal criteria"):
    editable=[m for m in ms if m["rule"] not in ("balanced","composite")]
    selected=st.selectbox("Metric",[m["name"] for m in editable])
    m=next(x for x in editable if x["name"]==selected)
    current_warn,current_danger=effective_thresholds(m)
    st.number_input("Supportive boundary",value=float(current_warn),key=f"warn_{m['series']}",format="%.4f")
    st.number_input("Restrictive boundary",value=float(current_danger),key=f"danger_{m['series']}",format="%.4f")
    st.caption("Changes apply immediately for this browser session. Edit metrics.yml to make them permanent for everyone.")
ms,loaded=build_all()
st.title(page)
st.caption("Signals describe whether conditions are supportive, neutral, or restrictive for CRE investment conditions. Direction alone is not treated as good or bad.")
if page=="Methodology & Sources":
    st.subheader("Signal methodology")
    st.write("Level rules compare the latest observation with configurable thresholds. Momentum rules compare the latest reading with the prior-year reading. Balanced metrics remain neutral until an investment-specific rule is approved. The composite is the weighted average of available signals, where Supportive = +1, Neutral = 0, and Restrictive = -1.")
    rows=[]
    for m in ms:
        df=loaded.get(m["series"],empty_df()); rows.append({"Metric":m["name"],"Page":m["page"],"Source":source_text(m).replace("Source: ",""),"Series":m["series"],"Latest observation":None if df.empty else df.date.max().date(),"Status":"Connected" if not df.empty else "Awaiting feed","Rating criteria":hurdle_text(m)})
    st.dataframe(pd.DataFrame(rows),use_container_width=True,hide_index=True)
    st.info("EXPORT means the app is looking for that series in data/manual_export.csv. RCA/MSCI metrics first attempt the optional API adapter, then fall back to data/rca_export.csv.")
else:
    chosen=[m for m in ms if m["page"]==page]
    cols=st.columns(3)
    for i,m in enumerate(chosen):
        with cols[i%3]: tile(m,loaded.get(m["series"],empty_df()))
