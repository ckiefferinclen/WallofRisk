from pathlib import Path
import yaml,pandas as pd,numpy as np,streamlit as st,plotly.graph_objects as go
from connectors.public_data import fred_series
from connectors.rca import load_export,load_api
from connectors.bts import monthly_port_trade
st.set_page_config(page_title='CRE Leading Indicators',page_icon='🏢',layout='wide')
PAGES=['CRE Macro Signal Board','Industrial Demand','Office Demand','Risk Monitor','Port Trade','Methodology & Sources']; COLORS={'Supportive':'#2E7D32','Neutral':'#F9A825','Restrictive':'#C62828','No data':'#7A869A'}
@st.cache_data(ttl=21600,show_spinner=False)
def config():
 ms=yaml.safe_load(Path('config/metrics.yml').read_text())['metrics']
 # Runtime migration means metrics.yml does not need manual editing for this release.
 for m in ms:
  if m.get('series')=='BTS_TOTAL_TEU' or m.get('name')=='Major U.S. port TEU throughput':
   m.update(name='Major U.S. port containerized import value',source='census_port',series='CENSUS_PORT_IMPORT_VALUE',unit='$bn',frequency='monthly',rule='momentum_higher',warn=-.03,danger=-.10,weight=1.0,definition='Combined monthly value of containerized vessel imports reported by the U.S. Census International Trade API across U.S. ports.')
 return ms
def empty():return pd.DataFrame(columns=['date','value'])
@st.cache_data(ttl=21600,show_spinner=False)
def raw(src,s):
 try:
  if src=='fred':return fred_series(s)
  if src=='rca':
   try:
    x=load_api(s,dict(st.secrets))
    if not x.empty:return x
   except Exception:pass
   return load_export(s)
 except Exception:pass
 return empty()
@st.cache_data(ttl=21600,show_spinner=False)
def port_data():
 try:
  key=st.secrets.get('CENSUS_API_KEY',None)
 except Exception:key=None
 try:return monthly_port_trade(api_key=key,months=48)
 except Exception:return pd.DataFrame(columns=['date','port','container_value','container_weight','vessel_weight','general_import_value'])
def yoy(x):
 if x.empty:return x
 z=x.copy();z['value']=z.value.pct_change(12)*100;return z.dropna()
def derived(s,L,P):
 d=lambda k:L.get(k,empty())
 if s in ('mortgage_spread','cap_treasury_spread'):
  a=d('commercial_mortgage_rate' if s=='mortgage_spread' else 'industrial_cap_rate');b=d('DGS10')
  if a.empty or b.empty:return empty()
  x=pd.merge_asof(a.sort_values('date'),b.sort_values('date'),on='date',direction='backward',suffixes=('_a','_b'));x['value']=(x.value_a-x.value_b)*100;return x[['date','value']]
 if s=='office_employment':
  ps=[d('USINFO'),d('USFIRE'),d('USPBS')]
  if any(x.empty for x in ps):return empty()
  x=ps[0].rename(columns={'value':'v0'})
  for i,p in enumerate(ps[1:],1):x=pd.merge_asof(x,p.rename(columns={'value':f'v{i}'}),on='date',direction='nearest',tolerance=pd.Timedelta('10d'))
  x['value']=x[['v0','v1','v2']].sum(axis=1,min_count=3);return x[['date','value']].dropna()
 if s=='energy_shock':
  x=d('GASDESW').copy()
  if x.empty:return x
  r=x.value.pct_change();x['value']=(r-r.rolling(52).mean())/r.rolling(52).std();return x[['date','value']].dropna()
 if s=='CPI_YOY':return yoy(d('CPIAUCSL'))
 if s=='CORE_CPI_YOY':return yoy(d('CPILFESL'))
 if s=='M2_YOY':return yoy(d('M2SL'))
 if s=='CENSUS_PORT_IMPORT_VALUE' and not P.empty:return P.groupby('date',as_index=False).container_value.sum().rename(columns={'container_value':'value'})
 return empty()
def point(x,months=0):
 if x.empty:return np.nan
 z=x[x.date<=x.date.max()-pd.DateOffset(months=months)];return z.iloc[-1].value if not z.empty else np.nan
def thresholds(m):return st.session_state.get('w_'+m['series'],float(m['warn'])),st.session_state.get('d_'+m['series'],float(m['danger']))
def signal(m,x):
 if x.empty:return 'No data',0
 c,y=point(x),point(x,12);chg=c/y-1 if pd.notna(y) and y else np.nan;r=m['rule'];w,d=thresholds(m)
 if r=='balanced':return 'Neutral',0
 if r=='composite':lab='Supportive' if c>=.25 else 'Restrictive' if c<=-.25 else 'Neutral';return lab,{'Supportive':1,'Neutral':0,'Restrictive':-1}[lab]
 v=chg if r.startswith('momentum') else c
 if pd.isna(v):return 'No data',0
 hi=r in ('higher_supportive','momentum_higher');lab=('Supportive' if v>=w else 'Restrictive' if v<=d else 'Neutral') if hi else ('Supportive' if v<=w else 'Restrictive' if v>=d else 'Neutral')
 return lab,{'Supportive':1,'Neutral':0,'Restrictive':-1}[lab]
def hurdle(m):
 r=m['rule'];w,d=thresholds(m)
 if r=='balanced':return 'Neutral pending an approved investment-specific hurdle.'
 if r=='composite':return 'Supportive ≥ +0.25 | Neutral between | Restrictive ≤ -0.25'
 f=lambda v:f'{v:+.1%} YoY' if r.startswith('momentum') else f'{v:g}{m["unit"] if m["unit"] in ("%","bp") else ""}'
 return f'Supportive ≥ {f(w)} | Neutral between | Restrictive ≤ {f(d)}' if r in ('higher_supportive','momentum_higher') else f'Supportive ≤ {f(w)} | Neutral between | Restrictive ≥ {f(d)}'
def build():
 ms=config();P=port_data();L={s:raw('fred',s) for s in ['USINFO','USFIRE','USPBS','CPIAUCSL','CPILFESL','M2SL']}
 for m in ms:
  if m['source'] in ('fred','rca'):L[m['series']]=raw(m['source'],m['series'])
 for m in ms:
  if m['source'] in ('derived','census_port') and m['series']!='composite':L[m['series']]=derived(m['series'],L,P)
 scores=[]
 for m in ms:
  if m['series']!='composite':
   lab,s=signal(m,L.get(m['series'],empty()))
   if lab!='No data' and m.get('weight',0)>0:scores.append((s,m['weight']))
 v=sum(s*w for s,w in scores)/sum(w for _,w in scores) if scores else np.nan;L['composite']=pd.DataFrame({'date':[pd.Timestamp.today().normalize()],'value':[v]}) if pd.notna(v) else empty();return ms,L,P
def fmt(v,u):
 if pd.isna(v):return 'Waiting for data'
 if u=='%':return f'{v:.2f}%'
 if u=='bp':return f'{v:,.0f} bp'
 if u=='$/gal':return f'${v:.2f}'
 if u in ('$bn','$mm'):return f'${v/1e9:,.2f} bn' if u=='$bn' and v>1e7 else f'${v:,.1f}'
 if u in ('lbs','kg'):return f'{v:,.0f}'
 if u=='score':return f'{v:+.2f}'
 return f'{v:,.2f}'
def bubble(m,x):
 lab,_=signal(m,x);c,p,t,y=point(x),point(x,1),point(x,3),point(x,12);arrow='—' if pd.isna(c) or pd.isna(t) else '↑' if c>t else '↓' if c<t else '→';yy=np.nan if pd.isna(c) or pd.isna(y) or y==0 else c/y-1
 with st.container(border=True):
  src='MSCI / RCA' if m['source']=='rca' else 'U.S. Census International Trade API' if m['source']=='census_port' else 'Calculated' if m['source']=='derived' else f'FRED · {m["series"]}'
  st.markdown(f"<div style='border-top:5px solid {COLORS[lab]};padding-top:8px'><b>{m['name']}</b><div style='font-size:.76rem;color:#6B778C'>Source: {src}</div><div style='font-size:1.85rem;font-weight:800'>{fmt(c,m['unit'])}</div><div style='color:{COLORS[lab]};font-weight:800'>{lab}</div></div>",unsafe_allow_html=True)
  a,b,z=st.columns(3);a.markdown(f'**Prior**<br>{fmt(p,m["unit"])}',unsafe_allow_html=True);b.markdown(f'**3-mo trend**<br>{arrow}',unsafe_allow_html=True);z.markdown(f'**YoY**<br>{"—" if pd.isna(yy) else f"{yy:+.1%}"}',unsafe_allow_html=True)
  st.caption('Latest observation: '+('None' if x.empty else x.date.max().strftime('%b %d, %Y')))
  if not x.empty:
   q=x.tail(60);fig=go.Figure(go.Scatter(x=q.date,y=q.value,mode='lines',line=dict(color=COLORS[lab],width=2)));fig.update_layout(height=185,margin=dict(l=8,r=8,t=8,b=8),showlegend=False,plot_bgcolor='#FAFBFC');fig.update_xaxes(showgrid=True,gridcolor='#D9DEE7',tickformat='%b\n%Y',nticks=5);fig.update_yaxes(showgrid=True,gridcolor='#D9DEE7',nticks=5);st.plotly_chart(fig,use_container_width=True,config={'displayModeBar':False},key='c_'+m['series'])
  else:st.info('Awaiting source data')
  st.caption(hurdle(m))
st.sidebar.title('CRE Dashboard');page=st.sidebar.radio('View',PAGES)
if st.sidebar.button('Refresh data',use_container_width=True):st.cache_data.clear();st.rerun()
ms=config()
with st.sidebar.expander('Adjust signal criteria'):
 e=[m for m in ms if m['rule'] not in ('balanced','composite')];n=st.selectbox('Metric',[m['name'] for m in e]);m=next(x for x in e if x['name']==n);w,d=thresholds(m);st.number_input('Supportive boundary',value=w,key='w_'+m['series'],format='%.4f');st.number_input('Restrictive boundary',value=d,key='d_'+m['series'],format='%.4f');st.caption('Session only. Edit metrics.yml for shared defaults.')
ms,L,P=build();st.title(page)
if page=='Port Trade':
 st.caption('Current monthly U.S. port trade from the Census International Trade API. This replaces the stale BTS TEU table.')
 if P.empty:st.error('The Census port feed could not be read. If usage limits become an issue, add an optional CENSUS_API_KEY in Streamlit secrets.')
 else:
  measures={'Containerized import value':('container_value','$bn'),'Containerized vessel weight':('container_weight','lbs'),'Total vessel weight':('vessel_weight','lbs'),'General import value':('general_import_value','$bn')};label=st.selectbox('Measure',list(measures));field,unit=measures[label];latest=P.date.max();st.metric('Latest observation',latest.strftime('%B %Y'))
  totals=P.groupby('port')[field].sum().nlargest(15).index; selected=st.multiselect('Ports',list(totals),default=list(totals[:10]));q=P[P.port.isin(selected)];wide=q.pivot_table(index='date',columns='port',values=field,aggfunc='sum');st.line_chart(wide)
  last=q[q.date==latest].sort_values(field,ascending=False);last['share']=last[field]/last[field].sum();st.dataframe(last.rename(columns={'port':'Port',field:label,'share':'Share'})[['Port',label,'Share']],hide_index=True,use_container_width=True,column_config={'Share':st.column_config.NumberColumn(format='%.1%%')})
  st.subheader('Port bubbles');cols=st.columns(3)
  for i,port in enumerate(selected):
   x=P[P.port==port][['date',field]].rename(columns={field:'value'});m={'name':port,'source':'census_port','series':f'PORT_{i}_{field}','unit':unit,'rule':'momentum_higher','warn':-.03,'danger':-.10}
   with cols[i%3]:bubble(m,x)
elif page=='Methodology & Sources':
 st.subheader('Signal methodology');st.write('Level rules compare the latest observation with thresholds. Momentum rules use year-over-year change. Balanced metrics remain neutral. The composite is a weighted average where Supportive = +1, Neutral = 0, Restrictive = -1.')
 rows=[{'Metric':m['name'],'Page':m['page'],'Source':m['source'].upper(),'Series':m['series'],'Latest observation':None if L.get(m['series'],empty()).empty else L[m['series']].date.max().date(),'Status':'Connected' if not L.get(m['series'],empty()).empty else 'Awaiting feed','Rating criteria':hurdle(m)} for m in ms];st.dataframe(pd.DataFrame(rows),use_container_width=True,hide_index=True);st.subheader('Metric definitions');st.dataframe(pd.DataFrame([{'Metric':m['name'],'Page':m['page'],'Definition':m.get('definition','')} for m in ms]),use_container_width=True,hide_index=True)
else:
 st.caption('Signals describe whether conditions are supportive, neutral, or restrictive. Direction alone is not treated as good or bad.')
 chosen=[m for m in ms if m['page']==page];groups=[('',chosen)] if page!='Office Demand' else [('Office Demand Indicators',[m for m in chosen if not m.get('section')]),('AI and Workplace Transformation',[m for m in chosen if m.get('section')])]
 for h,g in groups:
  if h:st.subheader(h)
  cols=st.columns(3)
  for i,m in enumerate(g):
   with cols[i%3]:bubble(m,L.get(m['series'],empty()))
