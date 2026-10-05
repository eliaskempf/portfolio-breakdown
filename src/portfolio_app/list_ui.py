"""Shared read-only lists: one visual style, sorting, row actions and scrolling policy.

Lists expand with the page by default. Callers opt into BOUNDED_LIST_HEIGHT for
long ETF look-through results. Spreadsheet editors remain native editable grids.
Keep asset calculations and filtering denominators in their domain modules.
"""
from dataclasses import asdict, dataclass
from hashlib import sha256
import json

import streamlit as st
from portfolio_app.settings import GAIN_COLOR, LOSS_COLOR

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
    width: int | None = None
    color_signed: bool = True


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
table.sized{table-layout:fixed}table.sized tr[data-position-id]>td{overflow:hidden;text-overflow:ellipsis}
th{position:sticky;top:0;background:var(--st-secondary-background-color);font-weight:600;z-index:1}th button{width:100%;padding:0;border:0;background:transparent;text-align:inherit;font-weight:inherit;cursor:pointer}
section[data-interactive="true"] tbody tr{cursor:pointer}tbody tr:hover,tbody tr:focus{background:var(--st-secondary-background-color)}tbody tr:last-child td{border-bottom:0}
tr:focus-visible{outline:2px solid var(--st-primary-color);outline-offset:-2px}input:focus-visible,button:focus-visible{outline:2px solid var(--st-primary-color);outline-offset:2px}
td.number,th.number{text-align:right;font-variant-numeric:tabular-nums}td:first-child{font-weight:500}
td.positive{color:var(--st-green-text-color,__GAIN_COLOR__)}td.negative{color:var(--st-red-text-color,__LOSS_COLOR__)}
td button{border:1px solid color-mix(in srgb,currentColor 20%,transparent);border-radius:4px;background:transparent;padding:4px 10px;cursor:pointer}
.preview-row{cursor:default!important}.preview-row>td{padding:0;background:color-mix(in srgb,var(--st-secondary-background-color) 55%,var(--st-background-color))}
.preview-panel{position:relative;display:grid;contain:inline-size;white-space:normal;font-size:13px;font-weight:400;padding-bottom:6px}
.preview-items{grid-column:1/-1;display:grid;grid-template-columns:subgrid;list-style:none;margin:0;padding:0}.preview-items li{grid-column:1/-1;display:grid;grid-template-columns:subgrid;min-height:30px}
.tree-branch{position:relative;display:flex;align-items:center;min-width:0;padding:5px 12px 5px 32px}
.tree-branch::before{content:'';position:absolute;left:12px;top:0;bottom:0;border-left:1px solid color-mix(in srgb,currentColor 27%,transparent)}
.tree-branch::after{content:'';position:absolute;left:12px;top:50%;width:13px;border-top:1px solid color-mix(in srgb,currentColor 27%,transparent)}
.preview-items li:last-child .tree-branch::before{bottom:50%}.tree-label{grid-column:1}.tree-label span{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.tree-total-parent{padding-right:40px}.tree-amount{justify-content:flex-end;padding-left:calc(var(--tree-value-root,12px) + 20px);font-variant-numeric:tabular-nums;white-space:nowrap}
.tree-amount::before,.tree-amount::after{left:var(--tree-value-root,12px)}
.tree-share{padding:5px 12px;align-self:center;text-align:right;opacity:.75;white-space:nowrap;font-variant-numeric:tabular-nums;font-size:13px}
.source-actions{display:flex;align-items:center;gap:14px}.source-details{border:0;padding:3px 0;color:var(--st-primary-color);font-weight:500}.source-details:hover{text-decoration:underline}.preview-more{grid-column:1/-1;margin:2px 12px 0 32px;font-size:12px;opacity:.65}
button[data-preview-toggle]{border:0;background:transparent;padding:0;font-weight:500}.empty{font-size:14px;opacity:.65}.badges{display:flex;gap:5px;flex-wrap:wrap;max-width:340px}.badge{font-size:12px;white-space:normal;border-radius:4px;padding:3px 6px;background:color-mix(in srgb,var(--badge-color) 15%,transparent);color:var(--st-text-color);border:1px solid color-mix(in srgb,var(--badge-color) 45%,transparent)}
""".replace("__GAIN_COLOR__", GAIN_COLOR).replace("__LOSS_COLOR__", LOSS_COLOR)

JS = """
export default function({parentElement:root,data,setTriggerValue}) {
  root.previewResizeObserver?.disconnect();
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
  const sized=columns.some(column=>column.width);
  table.classList.toggle('sized',sized);
  table.style.minWidth=sized?(columns.reduce((sum,column)=>sum+(column.width||160),0)+'px'):'';
  if (!columns.some(column => column.key === state.key)) {state.key=data.defaultSort;state.direction=-1;}
  const numeric=new Set(columns.filter(column=>column.numeric).map(column=>column.key));
  const element=(tag,text)=>{const node=document.createElement(tag);node.textContent=text;return node;};
  const open=(row,action='details')=>{if(!data.interactive)return;state.focus=row.id;state.focusAction='details';persist();setTriggerValue('open',{id:row.id,action,revision:data.revision,context:data.context});};
  const cell=(row,column)=>{
        const field=column.key;
        let value=row[field];
        if(column.numeric)value=value==null?'—':Number(value).toLocaleString(undefined,{maximumFractionDigits:column.decimals,minimumFractionDigits:column.decimals===10?0:column.decimals});
        if(column.display && row[column.display]!=null)value=row[column.display];
        const td=element('td',value==null||value===''?'—':String(value));
        if(column.numeric)td.className='number';
        if(column.width && !column.numeric)td.title=String(row[field]??'');
        if(column.tooltip)td.title=String(row[column.tooltip]||'');
        if(column.signed){
          td.title=String(row.note||column.help||'');
          if(row[field]>0){if(column.color_signed!==false)td.classList.add('positive');td.textContent='+'+td.textContent;}
          else if(row[field]<0 && column.color_signed!==false)td.classList.add('negative');
        }
        if(column.badges && Array.isArray(row[field])){
          td.replaceChildren();const badges=element('div','');badges.className='badges';
          for(const label of row[field]){const badge=element('span',label);badge.className='badge';badge.style.setProperty('--badge-color',column.badges[label]||'#7b8493');badges.appendChild(badge);}
          td.appendChild(badges);
        } else if(row[field]!=null && column.prefix)td.textContent=column.prefix+td.textContent;
    return td;
  };
  if (!data.rows.some(row=>row.id===state.expandedId)) state.expandedId=null;
  const updateToggle=(tr,row)=>{
    const toggle=tr.querySelector('[data-preview-toggle]');
    if(!toggle)return;
    const expanded=row.id===state.expandedId;
    toggle.textContent=(expanded?'▾ ':'▸ ')+String(row[data.previewColumn]??'Sources');
    toggle.setAttribute('aria-label',(expanded?'Hide':'Show')+' sources for '+row[columns[0].key]);
    toggle.setAttribute('aria-expanded',String(expanded));
    const details=tr.querySelector('.source-details');if(details)details.hidden=!expanded;
  };
  const preview=row=>{
    state.focus=row.id;state.focusAction='preview';
    const previous=state.expandedId;
    state.expandedId=previous===row.id?null:row.id;persist();
    // Change only the expanded row, without rebuilding a potentially long table
    // or triggering a Streamlit rerun.
    body.querySelector('.preview-row')?.remove();
    for(const id of [previous,row.id]){
      const tr=body.querySelector('tr[data-position-id="'+CSS.escape(id??'')+'"]');
      const item=data.rows.find(item=>item.id===id);
      if(tr&&item){
        updateToggle(tr,item);
        if(id===state.expandedId && data.previews?.[id])tr.after(previewRow(item));
      }
    }
    body.querySelector('tr[data-position-id="'+CSS.escape(row.id)+'"] [data-preview-toggle]')?.focus({preventScroll:true});
  };
  const activate=row=>data.previewColumn?preview(row):open(row);
  const alignPreview=(panel,row)=>{
    panel.style.gridTemplateColumns=Array.from(head.children,th=>th.getBoundingClientRect().width+'px').join(' ');
    const parent=body.querySelector('tr[data-position-id="'+CSS.escape(row.id)+'"]');
    const value=parent?.children[columns.findIndex(column=>column.key===data.previewValueColumn)];
    if(value){
      const range=document.createRange();range.selectNodeContents(value);
      panel.style.setProperty('--tree-value-root',(range.getBoundingClientRect().left-value.getBoundingClientRect().left)+'px');
    }
  };
  const previewRow=row=>{
    const outer=element('tr','');outer.className='preview-row';
    const td=element('td','');td.colSpan=columns.length+(data.editable?1:0);
    const panel=element('section','');panel.className='preview-panel';panel.setAttribute('role','region');
    panel.setAttribute('aria-label','Sources for '+row[columns[0].key]);
    const content=data.previews[row.id];
    alignPreview(panel,row);
    const items=element('ul','');items.className='preview-items';items.setAttribute('aria-label','Source contributions');
    for(const source of content.items){
      const item=element('li','');
      const label=element('span','');label.className='tree-branch tree-label';label.title=source.label;label.appendChild(element('span',source.label));
      const amount=element('span','');amount.className='tree-branch tree-amount';
      amount.style.gridColumn=String(columns.findIndex(column=>column.key===data.previewValueColumn)+1);
      amount.appendChild(element('span',source.amount));
      const share=element('span',source.share+' of asset');share.className='tree-share';share.title='Share of this asset’s total exposure';
      share.style.gridColumn=String(columns.findIndex(column=>column.key===data.previewShareColumn)+1);
      item.append(label,amount,share);
      items.appendChild(item);
    }
    panel.appendChild(items);
    if(content.more){const more=element('p','+'+content.more+' more in Details');more.className='preview-more';panel.appendChild(more);}
    td.appendChild(panel);outer.appendChild(td);return outer;
  };
  const render=()=>{
    const query=state.query.trim().toLocaleLowerCase();
    const rows=data.rows.filter(row=>(!data.searchLabel||data.searchFields.some(field=>String(row[field]??'').toLocaleLowerCase().includes(query))));
    const {key,direction}=state;
    rows.sort((a,b)=>{
      if(a[key]==null)return b[key]==null?0:1;if(b[key]==null)return -1;
      return direction*(numeric.has(key)?a[key]-b[key]:String(a[key]||'').localeCompare(String(b[key]||'')));
    });
    head.replaceChildren();body.replaceChildren();
    for(const {key:field,label,width} of columns){
      const th=element('th',''),button=element('button',label+(key===field?(direction===1?' ↑':' ↓'):''));
      if(width)th.style.width=width+'px';
      th.scope='col';if(numeric.has(field))th.className='number';
      if(key===field)th.setAttribute('aria-sort',direction===1?'ascending':'descending');
      button.type='button';button.onclick=()=>{state.key=field;state.direction=key===field?-direction:1;persist();render();head.querySelectorAll('button')[columns.findIndex(c=>c.key===field)].focus();};
      th.appendChild(button);head.appendChild(th);
    }
    if(data.editable){const actions=element('th','');actions.scope='col';actions.setAttribute('aria-label','Actions');head.appendChild(actions);}
    for(const row of rows){
      const tr=document.createElement('tr');tr.tabIndex=data.interactive?0:-1;tr.dataset.positionId=row.id;
      tr.onclick=event=>{if(!event.target.closest('button'))activate(row);};
      tr.onkeydown=event=>{
        if(event.target!==tr)return;
        if(event.key==='Enter'){event.preventDefault();activate(row);}
        if(event.key==='ArrowDown'||event.key==='ArrowUp'){event.preventDefault();let next=event.key==='ArrowDown'?tr.nextElementSibling:tr.previousElementSibling;while(next&&!next.dataset.positionId)next=event.key==='ArrowDown'?next.nextElementSibling:next.previousElementSibling;next?.focus();}
      };
      for(const column of columns){
        const td=cell(row,column);
        if(column.key===data.previewValueColumn)td.classList.add('tree-total-parent');
        if(column.key===data.previewColumn){
          const toggle=element('button','');
          toggle.type='button';toggle.dataset.previewToggle='true';
          toggle.onclick=()=>preview(row);
          const details=element('button','Details');details.type='button';details.className='source-details';
          details.setAttribute('aria-label','Details for '+row[columns[0].key]);details.title='Open asset details';details.onclick=()=>open(row);
          const actions=element('div','');actions.className='source-actions';actions.append(toggle,details);td.replaceChildren(actions);
        }
        tr.appendChild(td);
      }
      if(data.editable){const td=element('td',''),button=element('button','✎');button.type='button';
      button.setAttribute('aria-label',row.editLabel||'Edit '+String(row.name||row.id));
      button.title='Edit';button.onclick=()=>open(row,'edit');td.appendChild(button);tr.appendChild(td);}
      if(data.previewColumn)updateToggle(tr,row);
      body.appendChild(tr);
      if(row.id===state.expandedId && data.previews?.[row.id])body.appendChild(previewRow(row));
    }
    root.querySelector('.empty').hidden=rows.length!==0;
  };
  input.oninput=()=>{state.query=input.value;persist();render();};render();
  // Match the parent table's columns after resizing or toggling ticker display.
  // The preview remains local and does not cause a Streamlit rerun.
  const observer=new ResizeObserver(()=>{
    const panel=body.querySelector('.preview-panel');
    if(panel)alignPreview(panel,{id:state.expandedId});
  });
  observer.observe(table);root.previewResizeObserver=observer;
  if(state.focus&&!document.querySelector('[role="dialog"]')){
    const focused=body.querySelector('tr[data-position-id="'+CSS.escape(state.focus)+'"]');
    (state.focusAction==='preview'?focused?.querySelector('[data-preview-toggle]'):focused)?.focus({preventScroll:true});
  }
  return ()=>observer.disconnect();
}
"""


def frame_rows(table, *, id_column=None):
    """Serialize display rows with explicit nulls, preserving original columns."""
    rows = json.loads(table.to_json(orient='records', double_precision=15))
    for index, row in enumerate(rows):
        row['id'] = str(row[id_column]) if id_column else str(index)
    return rows


def render_list(rows, columns, *, key, context, title, on_open=None, editable=False,
                revision=None, max_height=None, search_label=None, search_fields=(), default_sort=None,
                preview_column=None, preview_value_column=None, preview_share_column=None, previews=None):
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

    from portfolio_app.currency_display import money_label
    monetary = {'Value', 'Cost', 'Gain', 'Total', 'Direct', 'ETF-derived', 'Position', 'Contribution',
                'Exposure', 'Reserved', 'Invested', 'Unallocated', 'Trade', 'Current', 'After', 'Budget'}
    specs = [asdict(column) for column in columns]
    for spec in specs:
        if spec['numeric'] and spec['label'] in monetary:
            spec['label'] = money_label(spec['label'])
    component = st.components.v2.component('portfolio_data_list', html=HTML, css=CSS, js=JS)
    component(key=key, data={'rows': rows, 'columns': specs,
        'revision': revision, 'context': context, 'title': title, 'interactive': bool(on_open),
        'editable': editable, 'maxHeight': max_height, 'searchLabel': search_label,
        'searchFields': search_fields, 'defaultSort': default_sort,
        'previewColumn': preview_column, 'previewValueColumn': preview_value_column, 'previewShareColumn': preview_share_column, 'previews': previews}, on_open_change=handle_open)
