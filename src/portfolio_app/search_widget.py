"""Debounced, keyboard-accessible listing search; provider text is never HTML."""

import streamlit as st
from uuid import uuid4

SEARCH_KEY = "position_edit_search_box"
HTML = """
<section aria-label="Find an investment">
 <label for="query">Find an investment</label>
 <div class="input-wrap"><span aria-hidden="true">⌕</span><input id="query" type="search" placeholder="Company, ETF, ticker or ISIN…" autocomplete="off" maxlength="120" aria-describedby="status" /><kbd aria-hidden="true">↵</kbd></div>
 <p id="status" role="status" aria-live="polite"></p>
 <div class="results" aria-label="Search results"></div>
</section>
"""
CSS = """
:host{font-family:var(--st-font,system-ui,sans-serif);color:var(--st-text-color)}*{box-sizing:border-box}
section{padding:4px 0 12px}label{display:block;font-size:16px;font-weight:600;margin-bottom:10px}
.input-wrap{display:flex;align-items:center;gap:12px;background:var(--st-secondary-background-color);border:1px solid color-mix(in srgb,currentColor 20%,transparent);border-radius:5px;padding:4px 12px}
.input-wrap:focus-within{outline:2px solid var(--st-primary-color);outline-offset:1px}.input-wrap>span{font-size:24px;opacity:.65}
input{min-width:0;width:100%;border:0;outline:0;background:transparent;color:inherit;padding:10px 0;font:inherit}input::placeholder{color:inherit;opacity:.5}kbd{opacity:.6;font-size:12px}
#status{font-size:12px;opacity:.7;min-height:18px;margin:8px 0}
button{font:inherit;cursor:pointer;color:inherit}
.results{display:grid;gap:8px;max-height:430px;overflow-y:auto;padding:2px}.result{border:1px solid color-mix(in srgb,currentColor 20%,transparent);background:var(--st-background-color);border-radius:5px;padding:14px}
.head{display:flex;gap:10px;align-items:flex-start}.monogram{background:var(--st-secondary-background-color);border-radius:4px;min-width:34px;height:34px;display:grid;place-items:center;font-weight:600;font-size:12px}
h3{font-size:14px;line-height:1.45;font-weight:600;margin:0 0 5px}.meta{opacity:.7;font-size:11px;display:flex;flex-wrap:wrap;gap:8px}.badge{background:var(--st-secondary-background-color);padding:1px 6px;border-radius:3px;font-weight:600}
.listings{display:flex;gap:7px;flex-wrap:wrap;margin-top:10px}.listing{border:1px solid color-mix(in srgb,currentColor 20%,transparent);background:var(--st-secondary-background-color);border-radius:4px;padding:8px 10px;text-align:left;font-size:12px}
summary{cursor:pointer;margin-top:12px;font-weight:600;font-size:13px}summary:focus-visible{outline:2px solid var(--st-primary-color)}.listing strong{color:var(--st-primary-color)}.listing span{opacity:.7;padding-left:7px;font-size:11px}.listing:hover,.listing:focus-visible{border-color:var(--st-primary-color);outline:2px solid var(--st-primary-color);outline-offset:1px}
.listing.selected{border-color:var(--st-primary-color)}@media(max-width:600px){.result{padding:12px}.listing{width:100%}.input-wrap{padding:2px 10px}}
"""
JS = """
export default function(component) {
 const {parentElement:root,data,setStateValue,setTriggerValue}=component;
 const input=root.querySelector('input'), results=root.querySelector('.results'), status=root.querySelector('#status');
 const normalize=text=>text.normalize('NFKC').trim().replace(/\\s+/g,' ').toLowerCase();
 if(input.dataset.context!==data.context){clearTimeout(input.searchTimer);input.value=data.query||'';input.dataset.context=data.context;}
 const publish=()=>{clearTimeout(input.searchTimer);if(input.isConnected&&input.dataset.context===data.context)setStateValue('query',input.value);};
 input.oninput=event=>{clearTimeout(input.searchTimer);results.replaceChildren();status.textContent=input.value.trim().length<2?'Type at least 2 characters to search.':'Searching…';if(!event.isComposing)input.searchTimer=setTimeout(publish,400);};
 input.oncompositionend=()=>{clearTimeout(input.searchTimer);input.searchTimer=setTimeout(publish,400);};
 input.onkeydown=event=>{
  if(event.key==='Enter'&&!event.isComposing){event.preventDefault();publish();}
  if(event.key==='ArrowDown'){event.preventDefault();results.querySelector('summary,button')?.focus();}
  if(event.key==='Escape'){input.value='';results.replaceChildren();publish();}
 };
 const element=(tag,text,cls)=>{const el=document.createElement(tag);if(text)el.textContent=text;if(cls)el.className=cls;return el;};
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
  let listingParent=card;
  if(group.listings.length>1){
   const chooser=element('details'),summary=element('summary','Choose listing · '+group.listings.length+' exchanges');
   summary.title='Choose the exchange and trading currency used for this holding';
   chooser.appendChild(summary);card.appendChild(chooser);listingParent=chooser;
  }
  for(const listing of group.listings){
   const button=element('button','','listing'+(data.selected===listing.ticker?' selected':''));button.type='button';
   button.setAttribute('aria-label','Select '+listing.ticker+' on '+listing.exchange);
   button.title='Use '+listing.ticker+' on '+listing.exchange+' to fill this holding’s investment details';
   button.appendChild(element('strong',(group.listings.length===1?'Select investment · ':'Select · ')+listing.ticker));button.appendChild(element('span',listing.exchange+(listing.currency?' · '+listing.currency:'')));
   button.onclick=()=>{if(normalize(input.value)===normalize(data.query))setTriggerValue('selected',{ticker:listing.ticker,query:data.query});};
   listings.appendChild(button);
  }
  listingParent.appendChild(listings);results.appendChild(card);
 }
 results.onkeydown=event=>{
  const buttons=Array.from(results.querySelectorAll('summary,details[open] button,article > .listings button')),index=buttons.indexOf(root.activeElement);
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
