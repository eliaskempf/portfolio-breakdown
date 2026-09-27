"""Shared read-only lists: one visual style, sorting, row actions and scrolling policy.

Lists expand with the page by default. Callers opt into BOUNDED_LIST_HEIGHT for
long ETF look-through results. Spreadsheet editors remain native editable grids.
Keep asset calculations and filtering denominators in their domain modules.
"""
from dataclasses import asdict, dataclass
from hashlib import sha256
import json

import streamlit as st

BOUNDED_LIST_HEIGHT = 620


@dataclass(frozen=True)
class ListColumn:
    key: str
    label: str
    numeric: bool = False
    decimals: int = 2
    signed: bool = False
    prefix: str = ''
    tooltip: str = ''
    help: str = ''
    badges: dict[str, str] | None = None
    display: str = ''


HTML = """
<section>
  <input type="search" hidden />
  <div class="scroll"><table>
    <thead><tr></tr></thead><tbody></tbody>
  </table></div>
  <p class="empty" hidden>No matching rows.</p>
</section>
"""

CSS = """
:host{font-family:var(--st-font,system-ui,sans-serif);color:var(--st-text-color)}
*{box-sizing:border-box}input,button{font:inherit;color:inherit}
input{width:min(100%,360px);background:var(--st-secondary-background-color);border:1px solid color-mix(in srgb,currentColor 20%,transparent);border-radius:5px;padding:9px 12px;margin-bottom:12px}
input::placeholder{color:inherit;opacity:.55}.scroll{overflow-x:auto;border:1px solid color-mix(in srgb,currentColor 15%,transparent);border-radius:5px}
table{width:100%;border-collapse:collapse;font-size:14px;text-align:left}th,td{padding:10px 12px;border-bottom:1px solid color-mix(in srgb,currentColor 12%,transparent);white-space:nowrap}
th{position:sticky;top:0;background:var(--st-secondary-background-color);font-weight:600;z-index:1}th button{width:100%;padding:0;border:0;background:transparent;text-align:inherit;font-weight:inherit;cursor:pointer}
section[data-interactive="true"] tbody tr{cursor:pointer}tbody tr:hover,tbody tr:focus{background:var(--st-secondary-background-color)}tbody tr:last-child td{border-bottom:0}
tr:focus-visible{outline:2px solid var(--st-primary-color);outline-offset:-2px}input:focus-visible,button:focus-visible{outline:2px solid var(--st-primary-color);outline-offset:2px}
td.number,th.number{text-align:right;font-variant-numeric:tabular-nums}td:first-child{font-weight:500}
td.positive{color:var(--st-green-text-color,#27836c)}td.negative{color:var(--st-red-text-color,#b84655)}
td button{border:1px solid color-mix(in srgb,currentColor 20%,transparent);border-radius:4px;background:transparent;padding:4px 10px;cursor:pointer}
.empty{font-size:14px;opacity:.65}.badges{display:flex;gap:5px;flex-wrap:wrap;max-width:340px}.badge{font-size:12px;white-space:normal;border-radius:4px;padding:3px 6px;background:color-mix(in srgb,var(--badge-color) 15%,transparent);color:var(--st-text-color);border:1px solid color-mix(in srgb,var(--badge-color) 45%,transparent)}
"""

JS = """
export default function({parentElement:root,data,setTriggerValue}) {
  const input=root.querySelector('input'), head=root.querySelector('thead tr'), body=root.querySelector('tbody');
  const section=root.querySelector('section'), viewport=root.querySelector('.scroll'), table=root.querySelector('table');
  section.dataset.interactive=String(data.interactive);
  section.setAttribute('aria-label',data.title);table.setAttribute('aria-label',data.title);
  viewport.style.maxHeight=data.maxHeight?data.maxHeight+'px':'none';
  viewport.style.overflowY=data.maxHeight?'auto':'';
  input.hidden=!data.searchLabel;input.setAttribute('aria-label',data.searchLabel||'Filter list');input.placeholder=(data.searchLabel||'Filter list')+'…';
  const storageKey='portfolio-list-'+data.context;
  let saved={};try{saved=JSON.parse(sessionStorage.getItem(storageKey)||'{}');}catch{}
  const state=root.listContext===data.context?root.listState:{key:data.defaultSort,direction:-1,query:'',...saved};
  root.listContext=data.context;root.listState=state;input.value=state.query;
  const persist=()=>{try{sessionStorage.setItem(storageKey,JSON.stringify(state));}catch{}};
  const columns=data.columns;
  if (!columns.some(column => column.key === state.key)) {state.key=data.defaultSort;state.direction=-1;}
  const numeric=new Set(columns.filter(column=>column.numeric).map(column=>column.key));
  const element=(tag,text)=>{const node=document.createElement(tag);node.textContent=text;return node;};
  const open=(row,action='details')=>{if(!data.interactive)return;state.focus=row.id;persist();setTriggerValue('open',{id:row.id,action,revision:data.revision,context:data.context});};
  const render=()=>{
    const query=state.query.trim().toLocaleLowerCase();
    const rows=data.rows.filter(row=>(!data.searchLabel||data.searchFields.some(field=>String(row[field]??'').toLocaleLowerCase().includes(query))));
    const {key,direction}=state;
    rows.sort((a,b)=>{
      if(a[key]==null)return b[key]==null?0:1;if(b[key]==null)return -1;
      return direction*(numeric.has(key)?a[key]-b[key]:String(a[key]||'').localeCompare(String(b[key]||'')));
    });
    head.replaceChildren();body.replaceChildren();
    for(const {key:field,label} of columns){
      const th=element('th',''),button=element('button',label+(key===field?(direction===1?' ↑':' ↓'):''));
      th.scope='col';if(numeric.has(field))th.className='number';
      if(key===field)th.setAttribute('aria-sort',direction===1?'ascending':'descending');
      button.type='button';button.onclick=()=>{state.key=field;state.direction=key===field?-direction:1;persist();render();head.querySelectorAll('button')[columns.findIndex(c=>c.key===field)].focus();};
      th.appendChild(button);head.appendChild(th);
    }
    if(data.editable){const actions=element('th','');actions.scope='col';actions.setAttribute('aria-label','Actions');head.appendChild(actions);}
    for(const row of rows){
      const tr=document.createElement('tr');tr.tabIndex=data.interactive?0:-1;tr.dataset.positionId=row.id;
      tr.onclick=event=>{if(!event.target.closest('button'))open(row);};
      tr.onkeydown=event=>{
        if(event.target!==tr)return;
        if(event.key==='Enter'){event.preventDefault();open(row);}
        if(event.key==='ArrowDown'||event.key==='ArrowUp'){event.preventDefault();(event.key==='ArrowDown'?tr.nextElementSibling:tr.previousElementSibling)?.focus();}
      };
      for(const column of columns){
        const field=column.key;
        let value=row[field];
        if(numeric.has(field))value=value==null?'—':Number(value).toLocaleString(undefined,{maximumFractionDigits:column.decimals,minimumFractionDigits:column.decimals===10?0:column.decimals});
        if(column.display && row[column.display]!=null)value=row[column.display];
        const td=element('td',value==null||value===''?'—':String(value));
        if(numeric.has(field))td.className='number';
        if(column.tooltip)td.title=String(row[column.tooltip]||'');
        if(column.signed){
          td.title=String(row.note||column.help||'');
          if(row[field]>0){td.classList.add('positive');td.textContent='+'+td.textContent;}
          else if(row[field]<0)td.classList.add('negative');
        }
        if(column.badges && Array.isArray(row[field])){
          td.replaceChildren();const badges=element('div','');badges.className='badges';
          for(const label of row[field]){const badge=element('span',label);badge.className='badge';badge.style.setProperty('--badge-color',column.badges[label]||'#7b8493');badges.appendChild(badge);}
          td.appendChild(badges);
        } else if(row[field]!=null && column.prefix)td.textContent=column.prefix+td.textContent;
        tr.appendChild(td);
      }
      if(data.editable){const td=element('td',''),button=element('button','✎');button.type='button';
      button.setAttribute('aria-label',row.editLabel||'Edit '+String(row.name||row.id));
      button.title='Edit';button.onclick=()=>open(row,'edit');td.appendChild(button);tr.appendChild(td);}
      body.appendChild(tr);
    }
    root.querySelector('.empty').hidden=rows.length!==0;
  };
  input.oninput=()=>{state.query=input.value;persist();render();};render();
  if(state.focus&&!document.querySelector('[role="dialog"]')){
    body.querySelector('tr[data-position-id="'+CSS.escape(state.focus)+'"]')?.focus({preventScroll:true});
  }
}
"""


def frame_rows(table, *, id_column=None):
    """Serialize display rows with explicit nulls, preserving original columns."""
    rows = json.loads(table.to_json(orient='records', double_precision=15))
    for index, row in enumerate(rows):
        row['id'] = str(row[id_column]) if id_column else str(index)
    return rows


def render_list(rows, columns, *, key, context, title, on_open=None, editable=False,
                revision=None, max_height=None, search_label=None, search_fields=(), default_sort=None):
    revision = revision or sha256(repr(rows).encode()).hexdigest()
    def handle_open():
        event = st.session_state.get(key, {}).get('open')
        if not event or not on_open:
            return
        if (event.get('context') != context or event.get('revision') != revision
                or event.get('id') not in {row['id'] for row in rows}):
            st.warning('The list changed. Select the row again from the refreshed list.')
            return
        if event.get('action') == 'details' or (editable and event.get('action') == 'edit'):
            on_open(event)

    component = st.components.v2.component('portfolio_data_list', html=HTML, css=CSS, js=JS)
    component(key=key, data={'rows': rows, 'columns': [asdict(column) for column in columns],
        'revision': revision, 'context': context, 'title': title, 'interactive': bool(on_open),
        'editable': editable, 'maxHeight': max_height, 'searchLabel': search_label,
        'searchFields': search_fields, 'defaultSort': default_sort}, on_open_change=handle_open)
