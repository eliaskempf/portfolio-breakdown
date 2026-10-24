"""Direct transaction editing and bulk selection, with no reruns for selection."""
import streamlit as st

from portfolio_app.list_ui import CSS as LIST_CSS

HTML = '''<section><div class="scroll"><table aria-label="Transactions">
<thead></thead><tbody></tbody></table></div>
<button class="remove" disabled>Remove selected (0)</button></section>'''
CSS = LIST_CSS + '''
.scroll{max-height:440px;overflow:auto}input[type=checkbox]{width:16px;height:16px;margin:0;cursor:pointer}
tr[data-transaction-id]:hover{background:color-mix(in srgb,currentColor 5%,transparent)}
tr[data-transaction-id]:focus{outline:2px solid var(--st-primary-color);outline-offset:-2px}
.remove{margin-top:12px;padding:7px 12px;border-radius:5px;border:1px solid color-mix(in srgb,currentColor 20%,transparent);background:transparent;cursor:pointer}
.remove:disabled{opacity:.45;cursor:default}td:last-child{width:60px}
'''
JS = r'''
export default function({parentElement, data, setTriggerValue}) {
  const root=parentElement, table=root.querySelector('table'), head=table.querySelector('thead'), body=table.querySelector('tbody');
  const state=root.transactionState?.revision===data.revision ? root.transactionState :
    {revision:data.revision, selected:new Set(), descending:true};
  root.transactionState=state;
  const removable=data.rows.filter(r=>r.removable);
  const remove=root.querySelector('.remove');
  const send=(action,ids)=>setTriggerValue('action',{action,ids,revision:data.revision});
  const open=row=>send('edit',[row.id]);
  const update=()=>{
    remove.textContent=`Remove selected (${state.selected.size})`;
    remove.disabled=!state.selected.size;
    const all=head.querySelector('input');
    all.checked=!!removable.length && state.selected.size===removable.length;
    all.indeterminate=state.selected.size>0 && !all.checked;
    body.querySelectorAll('input').forEach(input=>input.checked=state.selected.has(input.value));
  };
  const render=()=>{
    head.replaceChildren();body.replaceChildren();
    const header=document.createElement('tr'), checkCell=document.createElement('th'), all=document.createElement('input');
    all.type='checkbox';all.setAttribute('aria-label','Select all transactions');all.disabled=!removable.length;
    all.onchange=()=>{state.selected=new Set(all.checked?removable.map(r=>r.id):[]);update();};
    checkCell.append(all);header.append(checkCell);
    data.columns.forEach((label,index)=>{
      const th=document.createElement('th');
      if(index===0){
        const button=document.createElement('button');button.textContent='Date '+(state.descending?'↓':'↑');
        button.onclick=()=>{state.descending=!state.descending;render();};
        th.setAttribute('aria-sort',state.descending?'descending':'ascending');th.append(button);
      }else{th.textContent=label;}
      header.append(th);
    });
    const actions=document.createElement('th');actions.textContent='';header.append(actions);head.append(header);
    const rows=[...data.rows].sort((a,b)=>{
      const order=a.date.localeCompare(b.date)||a.sequence-b.sequence||a.order-b.order;
      return state.descending?-order:order;
    });
    rows.forEach(row=>{
      const tr=document.createElement('tr');tr.dataset.transactionId=row.id;tr.tabIndex=0;
      tr.title='Double-click to edit';
      tr.ondblclick=e=>{if(!e.target.closest('input,button'))open(row);};
      tr.onkeydown=e=>{if(e.key==='Enter' && e.target===tr){e.preventDefault();open(row);}};
      const cell=document.createElement('td');
      if(row.removable){
        const input=document.createElement('input');input.type='checkbox';input.value=row.id;
        input.setAttribute('aria-label','Select '+row.label);
        input.onchange=()=>{if(input.checked)state.selected.add(row.id);else state.selected.delete(row.id);update();};
        cell.append(input);
      }
      tr.append(cell);
      row.cells.forEach((text,index)=>{const td=document.createElement('td');td.textContent=text;
        if([2,3,4].includes(index))td.className='number';tr.append(td);});
      const action=document.createElement('td'),edit=document.createElement('button');edit.textContent='Edit';
      edit.setAttribute('aria-label','Edit '+row.label);edit.onclick=()=>open(row);action.append(edit);tr.append(action);
      body.append(tr);
    });
    update();
  };
  remove.onclick=()=>send('remove',[...state.selected]);
  render();
}
'''


def render_transaction_list(records, frame, *, key, revision, on_edit, on_remove):
    rows = [dict(id=event.id, date=event.date, sequence=event.sequence, order=index,
                 removable=event.kind != 'opening',
                 label=f'{event.date or "Undated"} {frame.iloc[index]["Transaction"]} · {event.quantity:g} units',
                 cells=[str(value) for value in frame.iloc[index]]) for index, event in enumerate(records)]
    def act():
        event = st.session_state.get(key, {}).get('action')
        if not event:
            return
        identities = event.get('ids', [])
        if event.get('revision') != revision or not identities or not set(identities) <= {row['id'] for row in rows}:
            st.warning('History changed. Select the transactions again.')
            return
        if event.get('action') == 'edit' and len(identities) == 1:
            on_edit(identities[0])
        elif event.get('action') == 'remove':
            on_remove(identities)
    component = st.components.v2.component('portfolio_transaction_list', html=HTML, css=CSS, js=JS)
    component(key=key, data=dict(rows=rows, columns=list(frame.columns), revision=revision), on_action_change=act)
