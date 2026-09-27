"""Accessible position list with stable row identities for opening the editor."""

from hashlib import sha256

import math

import streamlit as st
from portfolio_app.display_names import instrument_name


HTML = """
<section aria-label="Positions">
  <input type="search" aria-label="Filter positions" placeholder="Filter positions…" />
  <div class="scroll"><table aria-label="Positions">
    <thead><tr></tr></thead><tbody></tbody>
  </table></div>
  <p class="empty" hidden>No matching positions.</p>
</section>
"""

CSS = """
:host{font-family:var(--st-font,system-ui,sans-serif);color:var(--st-text-color)}
*{box-sizing:border-box}input,button{font:inherit;color:inherit}
input{width:min(100%,360px);background:var(--st-secondary-background-color);border:1px solid color-mix(in srgb,currentColor 20%,transparent);border-radius:5px;padding:9px 12px;margin-bottom:12px}
input::placeholder{color:inherit;opacity:.55}.scroll{max-height:580px;overflow:auto;border:1px solid color-mix(in srgb,currentColor 15%,transparent);border-radius:5px}
table{width:100%;border-collapse:collapse;font-size:14px;text-align:left}th,td{padding:10px 12px;border-bottom:1px solid color-mix(in srgb,currentColor 12%,transparent);white-space:nowrap}
th{position:sticky;top:0;background:var(--st-secondary-background-color);font-weight:600;z-index:1}th button{width:100%;padding:0;border:0;background:transparent;text-align:inherit;font-weight:inherit;cursor:pointer}
tbody tr{cursor:pointer}tbody tr:hover,tbody tr:focus{background:var(--st-secondary-background-color)}tbody tr:last-child td{border-bottom:0}
tr:focus-visible{outline:2px solid var(--st-primary-color);outline-offset:-2px}input:focus-visible,button:focus-visible{outline:2px solid var(--st-primary-color);outline-offset:2px}
td.number,th.number{text-align:right;font-variant-numeric:tabular-nums}td:first-child{font-weight:500}
td.positive{color:var(--st-green-text-color,#27836c)}td.negative{color:var(--st-red-text-color,#b84655)}
td button{border:1px solid color-mix(in srgb,currentColor 20%,transparent);border-radius:4px;background:transparent;padding:4px 10px;cursor:pointer}
.empty{font-size:14px;opacity:.65}
"""

JS = """
export default function({parentElement:root,data,setTriggerValue}) {
  const input=root.querySelector('input'), head=root.querySelector('thead tr'), body=root.querySelector('tbody'), scroll=root.querySelector('.scroll');
  const storageKey='portfolio-list-'+data.context;
  let saved={};try{saved=JSON.parse(sessionStorage.getItem(storageKey)||'{}');}catch{}
  const state=root.listContext===data.context?root.listState:{key:'value',direction:-1,query:'',scroll:0,...saved};
  root.listContext=data.context;root.listState=state;input.value=state.query;
  const persist=()=>{try{sessionStorage.setItem(storageKey,JSON.stringify(state));}catch{}};
  const columns=data.metricView&&data.metricView!=='Holdings'?
    [['name','Investment'],['value','Value (EUR)'],...(data.metricColumns||[])]:
    [['name','Investment'],
    [data.hasBuckets?'bucket':'portfolio',data.hasBuckets?'Category':'Portfolio / sleeve'],
    ['quantity','Quantity'],['value','Value (EUR)'],['allocation','Allocation (%)'],['gain',data.percent?'Return (%)':'Gain (EUR)']];
  const numeric=new Set(['quantity','value','allocation','gain',...(data.metricColumns||[]).map(c=>c[0])]);
  if(!columns.some(c=>c[0]===state.key)){state.key='value';state.direction=-1;}
  const element=(tag,text)=>{const node=document.createElement(tag);node.textContent=text;return node;};
  const open=(row,action='details')=>{state.focus=row.id;persist();setTriggerValue('open',{id:row.id,action,revision:data.revision,context:data.context});};
  const render=()=>{
    const query=state.query.trim().toLocaleLowerCase();
    const rows=data.rows.filter(row=>[row.name,row.fullName,row.ticker,row.isin,row.account,row.portfolio,row.bucket].some(value=>(value||'').toLocaleLowerCase().includes(query)));
    const {key,direction}=state;
    rows.sort((a,b)=>{
      if(a[key]==null)return b[key]==null?0:1;if(b[key]==null)return -1;
      return direction*(numeric.has(key)?a[key]-b[key]:String(a[key]||'').localeCompare(String(b[key]||'')));
    });
    head.replaceChildren();body.replaceChildren();
    for(const [field,label] of columns){
      const th=element('th',''),button=element('button',label+(key===field?(direction===1?' ↑':' ↓'):''));
      th.scope='col';if(numeric.has(field))th.className='number';
      if(key===field)th.setAttribute('aria-sort',direction===1?'ascending':'descending');
      button.type='button';button.onclick=()=>{state.key=field;state.direction=key===field?-direction:1;persist();render();head.querySelectorAll('button')[columns.findIndex(c=>c[0]===field)].focus();};
      th.appendChild(button);head.appendChild(th);
    }
    const actions=element('th','');actions.scope='col';actions.setAttribute('aria-label','Actions');head.appendChild(actions);
    for(const row of rows){
      const tr=document.createElement('tr');tr.tabIndex=0;tr.dataset.positionId=row.id;
      tr.onclick=event=>{if(!event.target.closest('button'))open(row);};
      tr.onkeydown=event=>{
        if(event.target!==tr)return;
        if(event.key==='Enter'){event.preventDefault();open(row);}
        if(event.key==='ArrowDown'||event.key==='ArrowUp'){event.preventDefault();(event.key==='ArrowDown'?tr.nextElementSibling:tr.previousElementSibling)?.focus();}
      };
      for(const [field] of columns){
        let value=row[field];
        if(numeric.has(field))value=row[field+'_display']||(value==null?'—':Number(value).toLocaleString(undefined,{maximumFractionDigits:field==='quantity'?10:2,minimumFractionDigits:field==='quantity'?0:2}));
        const td=element('td',value==null||value===''?'—':String(value));
        if(numeric.has(field))td.className='number';
        if(row[field+'_note'])td.title=row[field+'_note'];
        if(field==='name')td.title=[row.fullName,row.ticker,row.isin,row.account].filter(Boolean).join(' · ');
        if(field==='gain'){
          td.title=row.note||'Unrealized gain on recorded EUR cost';
          if(row.gain>0){td.classList.add('positive');td.textContent='+'+td.textContent;}
          else if(row.gain<0)td.classList.add('negative');
        }
        tr.appendChild(td);
      }
      const td=element('td',''),button=element('button','✎');button.type='button';
      button.setAttribute('aria-label','Edit '+row.name+(row.account?' · '+row.account:''));
      button.title='Edit position';button.onclick=()=>open(row,'edit');td.appendChild(button);tr.appendChild(td);body.appendChild(tr);
    }
    root.querySelector('.empty').hidden=rows.length!==0;
    scroll.scrollTop=state.scroll;
  };
  scroll.onscroll=()=>{state.scroll=scroll.scrollTop;persist();};
  input.oninput=()=>{state.query=input.value;state.scroll=0;persist();render();};render();
  if(state.focus&&!document.querySelector('[role="dialog"]')){
    body.querySelector('tr[data-position-id="'+CSS.escape(state.focus)+'"]')?.focus({preventScroll:true});
  }
}
"""


def list_context(path) -> str:
    return sha256(str(path.resolve()).encode()).hexdigest()[:16]


def position_rows(holdings, allocation=None, *, percent=False):
    buckets = {bucket.id: bucket.name for bucket in allocation.buckets} if allocation else {}
    total = holdings.current_value_eur.sum() if 'current_value_eur' in holdings else 0
    complete = 'current_value_eur' in holdings and holdings.current_value_eur.notna().all()
    def number(value):
        return float(value) if value is not None and math.isfinite(float(value)) else None
    rows = []
    for row in holdings.itertuples():
        value = number(getattr(row, 'current_value_eur', None))
        gain = number(getattr(row, 'unrealized_gain_eur', None))
        if percent and gain is not None:
            gain = number(getattr(row, 'return_pct', None))
        rows.append(dict(id=row.position_id, name=instrument_name(row), fullName=row.name,
            ticker=row.ticker, isin=row.isin, account=row.account, portfolio=row.portfolio,
            bucket=buckets.get(getattr(row, 'bucket_id', ''), 'Unassigned'), quantity=row.shares,
            value=value, allocation=100 * value / total if complete and total > 0 and value is not None else None,
            gain=gain, note=getattr(row, 'performance_note', '')))
    return rows


def render_position_list(path, snapshot, allocation=None, *, valued=None, percent=False, demo=False) -> None:
    from portfolio_app.position_metrics_ui import position_metric_toolbar, render_position_metric_sources
    holdings = snapshot.holdings if valued is None else valued
    view, metric_columns, metrics, detail = position_metric_toolbar(holdings, path.parent, demo=demo)
    rows = position_rows(holdings, allocation, percent=percent)
    instruments = holdings.set_index('position_id').id.to_dict()
    for row in rows:
        row.update(metrics.get(instruments[row['id']], {}))
    context = list_context(path)
    key = f'position_list_{context}'

    def open_position():
        event = st.session_state.get(key, {}).get('open')
        if event:
            st.session_state['position_edit_open_request'] = event

    component = st.components.v2.component('portfolio_position_list', html=HTML, css=CSS, js=JS)
    component(key=key, data={'rows': rows, 'revision': snapshot.revision, 'context': context,
                             'hasBuckets': bool(allocation), 'percent': percent,
                             'metricView': view, 'metricColumns': metric_columns}, on_open_change=open_position)
    render_position_metric_sources(detail)
