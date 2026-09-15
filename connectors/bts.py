from __future__ import annotations
import io, re, requests, pandas as pd

CATALOG = "https://api.us.socrata.com/api/catalog/v1"
DOMAIN = "data.bts.gov"
TABLEAU_CSV = "https://explore.dot.gov/t/BTS/views/MonthlyTEUDashboardNew_17697067597260/MonthlyData.csv?:showVizHome=no"
HEADERS = {"User-Agent": "Mozilla/5.0 CRE-leading-indicators/1.0"}

PORT_ALIASES = {
 "charleston":"Charleston, SC","houston":"Houston, TX","long beach":"Long Beach, CA",
 "los angeles":"Los Angeles, CA","nwsa":"Seattle/Tacoma, WA","seattle":"Seattle/Tacoma, WA",
 "tacoma":"Seattle/Tacoma, WA","oakland":"Oakland, CA","new york":"New York/New Jersey",
 "ny/nj":"New York/New Jersey","ny & nj":"New York/New Jersey","virginia":"Virginia",
 "savannah":"Savannah, GA","jacksonville":"Jacksonville, FL","miami":"Miami, FL"
}

def _port(v):
 t=re.sub(r"\s+"," ",str(v)).strip(); low=t.lower()
 for k,label in PORT_ALIASES.items():
  if k in low:return label
 return None

def _date(s):
 text=s.astype(str).str.strip().str.replace(r"^(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*[- ]",lambda m:m.group(1)+" ",regex=True)
 return pd.to_datetime(text,errors="coerce")

def _num(s):return pd.to_numeric(s.astype(str).str.replace(",","",regex=False).str.replace(r"[^0-9.\-]","",regex=True),errors="coerce")

def _parse(df):
 if df.empty:raise ValueError("empty table")
 df.columns=[str(c).strip() for c in df.columns]; low={c:c.lower() for c in df.columns}
 # long format
 pc=next((c for c,l in low.items() if "port" in l and "airport" not in l),None)
 dc=next((c for c,l in low.items() if any(k in l for k in ("date","month","period"))),None)
 vc=next((c for c,l in low.items() if "teu" in l and not any(k in l for k in ("port","date","month","rank","share"))),None)
 if pc and dc and vc:
  out=pd.DataFrame({"date":_date(df[dc]),"port":df[pc].map(_port),"value":_num(df[vc])}).dropna()
  if not out.empty:return out.groupby(["date","port"],as_index=False).value.sum()
 # wide format, date field may be misleadingly named Port in the old and some current tables
 best=None;count=0
 for c in df.columns:
  q=_date(df[c]);n=q.notna().sum()
  if n>count:best,count=c,n
 if best and count>=3:
  work=df.copy();work["date"]=_date(work[best]); cols=[c for c in df.columns if c!=best and _port(c)]
  if cols:
   out=work[["date"]+cols].melt("date",var_name="port",value_name="value");out["port"]=out.port.map(_port);out["value"]=_num(out.value)
   out=out.dropna()
   if not out.empty:return out.groupby(["date","port"],as_index=False).value.sum()
 raise ValueError("unrecognized columns: "+", ".join(df.columns))

def _socrata_candidates():
 params={"search_context":DOMAIN,"q":"monthly TEU port container","limit":100}
 r=requests.get(CATALOG,params=params,headers=HEADERS,timeout=45);r.raise_for_status()
 found=[]
 for item in r.json().get("results",[]):
  res=item.get("resource",{}); rid=res.get("id"); typ=res.get("type","")
  title=(res.get("name") or "").lower(); desc=(res.get("description") or "").lower()
  if rid and typ in ("dataset","filter") and "teu" in title+desc:
   found.append((rid,res.get("name",rid)))
 return found

def _from_socrata():
 good=[]; errors=[]
 for rid,title in _socrata_candidates():
  try:
   url=f"https://{DOMAIN}/resource/{rid}.json?$limit=50000"
   r=requests.get(url,headers=HEADERS,timeout=60);r.raise_for_status();out=_parse(pd.DataFrame(r.json()))
   if not out.empty:good.append((out.date.max(),rid,title,out))
  except Exception as e:errors.append(f"{rid}: {e}")
 if not good:raise RuntimeError("No current Socrata TEU dataset parsed. "+"; ".join(errors[:5]))
 good.sort(key=lambda x:x[0],reverse=True);latest,rid,title,out=good[0]
 # Never silently serve the known stale 2022 asset or another stale result.
 if latest.year<2024:raise RuntimeError(f"Newest Socrata TEU result ({rid}, {title}) ends {latest.date()}")
 out.attrs.update(feed=f"BTS Socrata {rid}: {title}",dataset_id=rid,stale_fallback=False);return out

def _from_tableau():
 r=requests.get(TABLEAU_CSV,headers=HEADERS,timeout=90);r.raise_for_status();out=_parse(pd.read_csv(io.BytesIO(r.content)))
 if out.empty or out.date.max().year<2024:raise RuntimeError(f"Tableau export ends {out.date.max() if not out.empty else 'empty'}")
 out.attrs.update(feed="BTS current Monthly TEU dashboard",dataset_id="MonthlyTEUDashboardNew",stale_fallback=False);return out

def monthly_teu():
 """Discover the newest public BTS TEU dataset; never fall back to 2022 data."""
 errors=[]
 for loader in (_from_socrata,_from_tableau):
  try:return loader().sort_values(["date","port"])[["date","port","value"]]
  except Exception as e:errors.append(str(e))
 raise RuntimeError("Current BTS TEU data unavailable. "+" | ".join(errors))
