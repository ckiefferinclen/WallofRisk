from pathlib import Path
import yaml,pandas as pd,numpy as np,streamlit as st,plotly.graph_objects as go
from connectors.public_data import fred_series
from connectors.rca import load_export,load_api
from connectors.bts import monthly_teu
st.set_page_config(page_title='CRE Leading Indicators',page_icon='🏢',layout='wide')
PAGES=['CRE Macro Signal Board','Industrial Demand','Office Demand','Risk Monitor','Ports','Methodology & Sources']; C={'Supportive':'#2E7D32','Neutral':'#F9A825','Restrictive':'#C62828','No data':'#7A869A'}
@st.cache_data(ttl=21600,show_spinner=False)
def cfg():return yaml.safe_load(Path('config/metrics.yml').read_text())['metrics']
def emp():return pd.DataFrame(columns=['date','value'])
@st.cache_data(ttl=21600,show_spinner=False)
def source(src,s):
 try:
  if src=='fred':return fred_series(s)
  if src=='rca':
   try:
    x=load_api(s,dict(st.secrets))
    if not x.empty:return x
   except Exception:pass
   return load_export(s)
 except Exception:pass
 return emp()
@st.cache_data(ttl=21600,show_spinner=False)
def ports():
 try:return monthly_teu()
 except Exception:return pd.DataFrame(columns=['date','port','value'])
def yoy_series(x,scale=100):
 if x.empty:return x
 z=x.copy();z['value']=z.value.pct_change(12)*scale;return z.dropna()
def derive(s,L,P):
 d=lambda k:L.get(k,emp())
 if s in ('mortgage_spread','cap_treasury_spread'):
  a=d('commercial_mortgage_rate' if s=='mortgage_spread' else 'industrial_cap_rate');b=d('DGS10')
  if a.empty or b.empty:return emp()
  x=pd.merge_asof(a.sort_values('date'),b.sort_values('date'),on='date',direction='backward',suffixes=('_a','_b'));x['value']=(x.value_a-x.value_b)*100;return x[['date','value']]
 if s=='office_employment':
  ps=[d('USINFO'),d('USFIRE'),d('USPBS')]
  if any(x.empty for x in ps):return emp()
  x=ps[0].rename(columns={'value':'v0'})
  for i,p in enumerate(ps[1:],1):x=pd.merge_asof(x,p.rename(columns={'value':f'v{i}'}),on='date',direction='nearest',tolerance=pd.Timedelta('10d'))
  x['value']=x[['v0','v1','v2']].sum(axis=1,min_count=3);return x[['date','value']].dropna()
 if s=='energy_shock':
  x=d('GASDESW').copy()
  if x.empty:return x
  r=x.value.pct_change();x['value']=(r-r.rolling(52).mean())/r.rolling(52).std();return x[['date','value']].dropna()
 if s=='CPI_YOY':return yoy_series(d('CPIAUCSL'))
 if s=='CORE_CPI_YOY':return yoy_series(d('CPILFESL'))
 if s=='M2_YOY':return yoy_series(d('M2SL'))
 if s=='BTS_TOTAL_TEU' and not P.empty:return P.groupby('date',as_index=False).value.sum().sort_values('date')
 return emp()
def pt(x,mo=0):
 if x.empty:return np.nan
 z=x[x.date<=x.date.max()-pd.DateOffset(months=mo)];return z.iloc[-1].value if not z.empty else np.nan
def bounds(m):return st.session_state.get('w'+m['series'],float(m['warn'])),st.session_state.get('d'+m['series'],float(m['danger']))
def rating(m,x):
 if x.empty:return 'No data',0
 v=pt(x);y=pt(x,12);chg=v/y-1 if pd.notna(y) and y else np.nan;r=m['rule'];w,d=bounds(m)
 if r=='balanced':return 'Neutral',0
 if r=='composite':lab='Supportive' if v>=.25 else 'Restrictive' if v<=-.25 else 'Neutral';return lab,{'Supportive':1,'Neutral':0,'Restrictive':-1}[lab]
 q=chg if r.startswith('momentum') else v
 if pd.isna(q):return 'No data',0
 hi=r in ('higher_supportive','momentum_higher');lab=('Supportive' if q>=w else 'Restrictive' if q<=d else 'Neutral') if hi else ('Supportive' if q<=w else 'Restrictive' if q>=d else 'Neutral')
 return lab,{'Supportive':1,'Neutral':0,'Restrictive':-1}[lab]
def hurdle(m):
 r=m['rule'];w,d=bounds(m)
 if r=='balanced':return 'Neutral pending an approved investment-specific hurdle.'
 if r=='composite':return 'Supportive ≥ +0.25 | Neutral between | Restrictive ≤ -0.25'
 f=lambda v:f'{v:+.1%} YoY' if r.startswith('momentum') else f'{v:g}{m["unit"] if m["unit"] in ("%","bp") else ""}'
 return f'Supportive ≥ {f(w)} | Neutral between | Restrictive ≤ {f(d)}' if r in ('higher_supportive','momentum_higher') else f'Supportive ≤ {f(w)} | Neutral between | Restrictive ≥ {f(d)}'
def build():
 ms=cfg();P=ports();L={s:source('fred',s) for s in ['USINFO','USFIRE','USPBS','CPIAUCSL','CPILFESL','M2SL']}
 for m in ms:
  if m['source'] in ('fred','rca'):L[m['series']]=source(m['source'],m['series'])
 for m in ms:
  if m['source'] in ('derived','bts') and m['series']!='composite':L[m['series']]=derive(m['series'],L,P)
 scores=[]
 for m in ms:
  if m['series']!='composite':
   lab,s=rating(m,L.get(m['series'],emp()))
   if lab!='No data' and m['weight']>0:scores.append((s,m['weight']))
 v=sum(s*w for s,w in scores)/sum(w for _,w in scores) if scores else np.nan;L['composite']=pd.DataFrame({'date':[pd.Timestamp.today().normalize()],'value':[v]}) if pd.notna(v) else emp();return ms,L,P
def fmt(v,u):
 if pd.isna(v):return 'Waiting for data'
 if u=='%':return f'{v:.2f}%'
 if u=='bp':return f'{v:,.0f} bp'
 if u=='$/gal':return f'${v:.2f}'
 if u in ('$bn','$mm'):return f'${v:,.1f}'
 if u=='TEUs':return f'{v:,.0f}'
 if u=='score':return f'{v:+.2f}'
 return f'{v:,.2f}'
def bubble(m,x):
 lab,_=rating(m,x);v,p,t,y=pt(x),pt(x,1),pt(x,3),pt(x,12);arrow='—' if pd.isna(v) or pd.isna(t) else '↑' if v>t else '↓' if v<t else '→';yy=np.nan if pd.isna(v) or pd.isna(y) or y==0 else v/y-1
 with st.container(border=True):
  src='MSCI / RCA' if m['source']=='rca' else 'BTS' if m['source']=='bts' else 'Calculated' if m['source']=='derived' else f'FRED · {m["series"]}'
  st.markdown(f"<div style='border-top:5px solid {C[lab]};padding-top:8px'><b>{m['name']}</b><div style='font-size:.76rem;color:#6B778C'>Source: {src}</div><div style='font-size:1.85rem;font-weight:800'>{fmt(v,m['unit'])}</div><div style='color:{C[lab]};font-weight:800'>{lab}</div></div>",unsafe_allow_html=True)
  a,b,c=st.columns(3);a.markdown(f'**Prior**<br>{fmt(p,m["unit"])}',unsafe_allow_html=True);b.markdown(f'**3-mo trend**<br>{arrow}',unsafe_allow_html=True);c.markdown(f'**YoY**<br>{"—" if pd.isna(yy) else f"{yy:+.1%}"}',unsafe_allow_html=True)
  st.caption('Latest observation: '+('None' if x.empty else x.date.max().strftime('%b %d, %Y')))
  if not x.empty:
   q=x.tail(60);fig=go.Figure(go.Scatter(x=q.date,y=q.value,mode='lines',line=dict(color=C[lab],width=2)));fig.update_layout(height=185,margin=dict(l=8,r=8,t=8,b=8),showlegend=False,plot_bgcolor='#FAFBFC');fig.update_xaxes(showgrid=True,gridcolor='#D9DEE7',tickformat='%b\n%Y',nticks=5);fig.update_yaxes(showgrid=True,gridcolor='#D9DEE7',nticks=5);st.plotly_chart(fig,use_container_width=True,config={'displayModeBar':False},key='x'+m['series'])
  else:st.info('Awaiting source data')
  st.caption(hurdle(m))
st.sidebar.title('CRE Dashboard');page=st.sidebar.radio('View',PAGES)
if st.sidebar.button('Refresh data',use_container_width=True):st.cache_data.clear();st.rerun()
ms=cfg()
with st.sidebar.expander('Adjust signal criteria'):
 e=[m for m in ms if m['rule'] not in ('balanced','composite')];name=st.selectbox('Metric',[m['name'] for m in e]);m=next(x for x in e if x['name']==name);w,d=bounds(m);st.number_input('Supportive boundary',value=w,key='w'+m['series'],format='%.4f');st.number_input('Restrictive boundary',value=d,key='d'+m['series'],format='%.4f');st.caption('Session only. Edit metrics.yml for shared defaults.')
ms,L,P=build();st.title(page)
if page=='Ports':
 st.caption('Monthly container throughput from the BTS open-data feed. The latest actual observation date is shown below.')
 if P.empty:st.error('The BTS feed could not be read. Check the app logs and connectors/bts.py.')
 else:
  latest=P.date.max(); st.metric('Latest BTS observation',latest.strftime('%B %Y'))
  ports_selected=st.multiselect('Ports',sorted(P.port.unique()),default=sorted(P.port.unique()))
  q=P[P.port.isin(ports_selected)];wide=q.pivot_table(index='date',columns='port',values='value',aggfunc='sum')
  st.line_chart(wide)
  latest_rows=q[q.date==latest].sort_values('value',ascending=False);latest_rows['share']=latest_rows.value/latest_rows.value.sum(); st.dataframe(latest_rows.rename(columns={'port':'Port','value':'Latest monthly TEUs','share':'Share of selected ports'})[['Port','Latest monthly TEUs','Share of selected ports']],hide_index=True,use_container_width=True,column_config={'Share of selected ports':st.column_config.NumberColumn(format='%.1%%')})
  st.subheader('Port bubbles');cols=st.columns(3)
  for i,port in enumerate(sorted(P.port.unique())):
   x=P[P.port==port][['date','value']];m={'name':port,'source':'bts','series':'PORT_'+str(i),'unit':'TEUs','rule':'momentum_higher','warn':-.03,'danger':-.10}
   with cols[i%3]:bubble(m,x)
elif page=='Methodology & Sources':
 st.subheader('Signal methodology');st.write('Level rules compare the latest observation with thresholds. Momentum rules use year-over-year change. Balanced metrics remain neutral. The composite is a weighted average where Supportive = +1, Neutral = 0, Restrictive = -1.')
 rows=[{'Metric':m['name'],'Page':m['page'],'Source':m['source'].upper(),'Series':m['series'],'Latest observation':None if L.get(m['series'],emp()).empty else L[m['series']].date.max().date(),'Status':'Connected' if not L.get(m['series'],emp()).empty else 'Awaiting feed','Rating criteria':hurdle(m)} for m in ms];st.dataframe(pd.DataFrame(rows),use_container_width=True,hide_index=True)
 st.subheader('Metric definitions');st.dataframe(pd.DataFrame([{'Metric':m['name'],'Page':m['page'],'Definition':m['definition']} for m in ms]),use_container_width=True,hide_index=True)
else:
 st.caption('Signals describe whether conditions are supportive, neutral, or restrictive. Direction alone is not treated as good or bad.')
 chosen=[m for m in ms if m['page']==page];groups=[('',chosen)] if page!='Office Demand' else [('Office Demand Indicators',[m for m in chosen if not m.get('section')]),('AI and Workplace Transformation',[m for m in chosen if m.get('section')])]
 for h,g in groups:
  if h:st.subheader(h)
  cols=st.columns(3)
  for i,m in enumerate(g):
   with cols[i%3]:bubble(m,L.get(m['series'],emp()))
