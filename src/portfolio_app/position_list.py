"""Accessible position list with stable row identities for opening the editor."""

from hashlib import sha256

import streamlit as st


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
td button{border:1px solid color-mix(in srgb,currentColor 20%,transparent);border-radius:4px;background:transparent;padding:4px 10px;cursor:pointer}
.empty{font-size:14px;opacity:.65}
"""

JS = """
export default function({parentElement:root,data,setTriggerValue}) {
  const input=root.querySelector('input'), head=root.querySelector('thead tr'), body=root.querySelector('tbody');
  // Keep sort/filter through harmless reruns, but not workspace changes.
  if(root.listContext!==data.context){root.listContext=data.context;root.listSort={key:'',direction:1};input.value='';}
  const columns=[['name','Investment'],['account','Account'],['portfolio','Portfolio / sleeve'],
    ...(data.hasBuckets?[['bucket','Category']]:[]),['quantity','Quantity'],...(data.metricColumns||[])];
  const numeric=new Set(['quantity',...(data.metricColumns||[]).map(c=>c[0])]);
  const element=(tag,text)=>{const node=document.createElement(tag);node.textContent=text;return node;};
  const open=row=>setTriggerValue('open',{id:row.id,revision:data.revision,context:data.context});
  const render=()=>{
    const query=input.value.trim().toLocaleLowerCase();
    const rows=data.rows.filter(row=>[row.name,row.ticker,row.account,row.portfolio,row.bucket].some(value=>(value||'').toLocaleLowerCase().includes(query)));
    const {key,direction}=root.listSort;
    if(key)rows.sort((a,b)=>{
      if(a[key]==null)return b[key]==null?0:1;if(b[key]==null)return -1;
      return direction*(numeric.has(key)?a[key]-b[key]:String(a[key]||'').localeCompare(String(b[key]||'')));
    });
    head.replaceChildren();body.replaceChildren();
    for(const [field,label] of columns){
      const th=element('th',''),button=element('button',label+(key===field?(direction===1?' ↑':' ↓'):''));
      th.scope='col';if(numeric.has(field))th.className='number';
      if(key===field)th.setAttribute('aria-sort',direction===1?'ascending':'descending');
      button.type='button';button.onclick=()=>{root.listSort={key:field,direction:key===field?-direction:1};render();head.querySelectorAll('button')[columns.findIndex(c=>c[0]===field)].focus();};
      th.appendChild(button);head.appendChild(th);
    }
    const actions=element('th','');actions.scope='col';actions.setAttribute('aria-label','Actions');head.appendChild(actions);
    for(const row of rows){
      const tr=document.createElement('tr');tr.tabIndex=0;tr.dataset.positionId=row.id;
      tr.ondblclick=event=>{if(!event.target.closest('button'))open(row);};
      tr.onkeydown=event=>{
        if(event.target!==tr)return;
        if(event.key==='Enter'){event.preventDefault();open(row);}
        if(event.key==='ArrowDown'||event.key==='ArrowUp'){event.preventDefault();(event.key==='ArrowDown'?tr.nextElementSibling:tr.previousElementSibling)?.focus();}
      };
      for(const [field] of columns){
        const value=row[field];
        const td=element('td',numeric.has(field)?(value==null?(row[field+'_display']||'—'):Number(value).toLocaleString(undefined,{maximumFractionDigits:field==='quantity'?10:2})):(value||'—'));
        if(numeric.has(field))td.className='number';
        if(row[field+'_note'])td.title=row[field+'_note'];
        tr.appendChild(td);
      }
      const td=element('td',''),button=element('button','Edit');button.type='button';
      button.setAttribute('aria-label','Edit '+row.name+(row.account?' · '+row.account:''));
      button.onclick=()=>open(row);td.appendChild(button);tr.appendChild(td);body.appendChild(tr);
    }
    root.querySelector('.empty').hidden=rows.length!==0;
  };
  input.oninput=render;render();
}
"""


def list_context(path) -> str:
    return sha256(str(path.resolve()).encode()).hexdigest()[:16]


def render_position_list(path, snapshot, allocation=None, *, demo=False) -> None:
    from portfolio_app.analytics_ui import position_metric_controls
    metric_columns, metrics = position_metric_controls(snapshot.holdings, path.parent, demo=demo)
    buckets = {bucket.id: bucket.name for bucket in allocation.buckets} if allocation else {}
    rows = [dict(id=row.position_id, name=row.name, ticker=row.ticker, account=row.account,
                 portfolio=row.portfolio, bucket=buckets.get(getattr(row, 'bucket_id', ''), getattr(row, 'bucket_id', '')),
                 quantity=row.shares, **metrics.get(row.id, {})) for row in snapshot.holdings.itertuples()]
    context = list_context(path)
    key = f'position_list_{context}'

    def open_position():
        event = st.session_state.get(key, {}).get('open')
        if event:
            st.session_state['position_edit_open_request'] = event

    component = st.components.v2.component('portfolio_position_list', html=HTML, css=CSS, js=JS)
    component(key=key, data={'rows': rows, 'revision': snapshot.revision, 'context': context,
                             'hasBuckets': bool(allocation), 'metricColumns': metric_columns}, on_open_change=open_position)
