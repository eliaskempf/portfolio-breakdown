"""Debounced, keyboard-accessible listing search; provider text is never HTML."""

import streamlit as st
from uuid import uuid4

SEARCH_KEY = "position_edit_search_box"
HTML = """
<section aria-label="Find an investment">
 <label for="query">Find an investment</label>
 <div class="input-wrap"><span aria-hidden="true">⌕</span><input id="query" type="search" placeholder="Company, ETF, ticker or ISIN…" autocomplete="off" maxlength="120" aria-describedby="status" /><kbd aria-hidden="true">↵</kbd></div>
 <p id="status" role="status" aria-live="polite"></p>
 <div class="suggestions" aria-label="Suggested searches"></div>
 <div class="results" aria-label="Search results"></div>
</section>
"""
CSS = """
:host{font-family:Inter,ui-sans-serif,system-ui,sans-serif;color:#172b32}*{box-sizing:border-box}
section{padding:4px 0 12px}label{display:block;font-size:18px;font-weight:650;letter-spacing:-.4px;margin-bottom:12px}
.input-wrap{display:flex;align-items:center;gap:12px;background:#fff;border:1px solid #cbd7d7;border-radius:12px;padding:4px 16px;box-shadow:0 3px 12px #183d3d06}
.input-wrap:focus-within{border-color:#187c72;box-shadow:0 0 0 3px #187c721a}.input-wrap>span{font-size:30px;color:#52736f}
input{min-width:0;width:100%;border:0;outline:0;background:transparent;color:#172b32;padding:13px 0;font:inherit;font-size:16px}input::placeholder{color:#7b8e93}kbd{color:#7b8e93;font-size:14px}
#status{font-size:12px;color:#647b81;min-height:18px;margin:10px 0}.suggestions{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:10px}
button{font:inherit;cursor:pointer}.suggestions button{border:1px solid #dfe7e6;border-radius:20px;background:#f5f9f8;color:#3d6560;padding:6px 13px;font-size:12px}
.results{display:grid;gap:10px;max-height:430px;overflow-y:auto;padding:2px}.result{border:1px solid #dfe7e6;background:#fff;border-radius:12px;padding:16px}
.head{display:flex;gap:12px;align-items:flex-start}.monogram{background:#edf5f2;color:#2d7268;border-radius:10px;min-width:40px;height:40px;display:grid;place-items:center;font-weight:700;font-size:13px}
h3{font-size:14px;line-height:1.45;font-weight:650;margin:0 0 5px}.meta{color:#698087;font-size:11px;display:flex;flex-wrap:wrap;gap:8px}.badge{background:#e6f2ed;color:#256f5d;padding:1px 6px;border-radius:4px;font-weight:600}
.listings{display:flex;gap:7px;flex-wrap:wrap;margin-top:12px}.listing{border:1px solid #d3e2dd;background:#fafcfb;color:#284f47;border-radius:8px;padding:8px 10px;text-align:left;font-size:12px}
.listing strong{letter-spacing:.3px}.listing span{color:#617a73;padding-left:7px;font-size:11px}.listing:hover,.listing:focus-visible{background:#eaf4ef;border-color:#187c72;outline:2px solid #187c7240;outline-offset:1px}
.listing.selected{background:#e2f0e9;border-color:#187c72}@media(max-width:600px){.result{padding:12px}.listing{width:100%}.input-wrap{padding:2px 10px}}
"""
JS = """
export default function(component) {
 const {parentElement:root,data,setStateValue,setTriggerValue}=component;
 const input=root.querySelector('input'), results=root.querySelector('.results'), status=root.querySelector('#status'), suggestions=root.querySelector('.suggestions');
 const normalize=text=>text.normalize('NFKC').trim().replace(/\\s+/g,' ').toLowerCase();
 if(input.dataset.context!==data.context){clearTimeout(input.searchTimer);input.value=data.query||'';input.dataset.context=data.context;}
 const publish=()=>{clearTimeout(input.searchTimer);if(input.isConnected&&input.dataset.context===data.context)setStateValue('query',input.value);};
 input.oninput=event=>{clearTimeout(input.searchTimer);results.replaceChildren();status.textContent=input.value.trim().length<2?'Type at least 2 characters to search.':'Searching…';if(!event.isComposing)input.searchTimer=setTimeout(publish,400);};
 input.oncompositionend=()=>{clearTimeout(input.searchTimer);input.searchTimer=setTimeout(publish,400);};
 input.onkeydown=event=>{
  if(event.key==='Enter'&&!event.isComposing){event.preventDefault();publish();}
  if(event.key==='ArrowDown'){event.preventDefault();results.querySelector('button')?.focus();}
  if(event.key==='Escape'){input.value='';results.replaceChildren();publish();}
 };
 const element=(tag,text,cls)=>{const el=document.createElement(tag);if(text)el.textContent=text;if(cls)el.className=cls;return el;};
 suggestions.replaceChildren();
 if(!input.value.trim())for(const query of ['VanEck','Semiconductors','NVIDIA']){
  const button=element('button',query);button.type='button';button.onclick=()=>{input.value=query;input.focus();publish();};suggestions.appendChild(button);
 }
 // Suppress responses for an older query while the user is still typing.
 if(normalize(input.value)!==normalize(data.query||''))return;
 status.textContent=data.message;results.replaceChildren();
 for(const group of data.groups){
  const card=element('article','','result'),head=element('div','','head'),info=element('div'),meta=element('div','','meta');
  head.appendChild(element('div',group.name.split(' ').slice(0,2).map(w=>w[0]).join('').toUpperCase(),'monogram'));
  info.appendChild(element('h3',group.name));meta.appendChild(element('span',group.kind));
  if(group.ucits)meta.appendChild(element('span','UCITS','badge'));
  if(group.isin)meta.appendChild(element('span',group.isin));
  info.appendChild(meta);head.appendChild(info);card.appendChild(head);
  const listings=element('div','','listings');
  for(const listing of group.listings){
   const button=element('button','','listing'+(data.selected===listing.ticker?' selected':''));button.type='button';
   button.setAttribute('aria-label','Select '+listing.ticker+' on '+listing.exchange);
   button.appendChild(element('strong',listing.ticker));button.appendChild(element('span',listing.exchange+(listing.currency?' · '+listing.currency:'')));
   button.onclick=()=>{if(normalize(input.value)===normalize(data.query))setTriggerValue('selected',{ticker:listing.ticker,query:data.query});};
   listings.appendChild(button);
  }
  card.appendChild(listings);results.appendChild(card);
 }
 results.onkeydown=event=>{
  const buttons=Array.from(results.querySelectorAll('button')),index=buttons.indexOf(root.activeElement);
  if(event.key==='ArrowDown'){event.preventDefault();buttons[Math.min(index+1,buttons.length-1)]?.focus();}
  if(event.key==='ArrowUp'){event.preventDefault();if(index<=0)input.focus();else buttons[index-1]?.focus();}
  if(event.key==='Escape')input.focus();
 };
}
"""

def render_search_box(query: str, groups: list[dict], message: str, selected: str = "") -> dict | None:
    # Registration is idempotent for identical definitions, and must happen in
    # the current runtime (AppTest and server restarts use separate registries).
    _search_box = st.components.v2.component("instrument_search", html=HTML, css=CSS, js=JS)
    generation = st.session_state.setdefault("position_edit_search_generation", uuid4().hex)
    result = _search_box(key=SEARCH_KEY, data={"query": query, "groups": groups, "message": message, "selected": selected, "context": generation},
                         default={"query": ""}, on_query_change=lambda: None, on_selected_change=lambda: None)
    return result.get("selected")
