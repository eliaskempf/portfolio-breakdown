"""Browser presentation of superseded Streamlit elements during streamed reruns."""

SCRIPT = r"""
<script>
(() => {
  if (window.__portfolioRerunDisplay) return;
  window.__portfolioRerunDisplay = true;
  const leaf = '[data-testid="stElementContainer"]';
  const retired = 'data-portfolio-superseded';
  const previous = new WeakMap();
  const marked = new Set();
  // Compare within the owning view. Two independent panels may use the same labels.
  const scopeOf = el => el.parentElement?.closest(
    '[class*="st-key-"],[role="dialog"],[role="tabpanel"],[data-testid="stPopoverBody"],[data-testid="stForm"],[data-testid="stExpander"],[data-testid="stSidebar"],[data-testid="stMain"]');
  function identity(el) {
    const content = el.firstElementChild;
    if (!content) return '';
    const key = [...el.classList].find(c => c.startsWith('st-key-'));
    if (key) return key;
    // Unkeyed text and controls can be matched by their displayed content.
    // Opaque canvases, custom HTML and charts need explicit identity.
    const type = content.dataset.testid;
    if (!type || /Chart|DataFrame|Editor|Component|Html|Skeleton|Image/.test(type)) return '';
    const text = content.textContent.trim();
    if (!text) return '';
    return type + '\n' + (content.querySelector('[data-testid="stCaptionContainer"]') ? 'caption:' : '') + text;
  }
  let pending = false;
  let hasStale = false;
  function reconcile() {
    pending = false;
    const stale = [...document.querySelectorAll(leaf+'[data-stale="true"]')];
    hasStale = stale.length > 0;
    const next = new Set();
    const scopes = new Map();
    for (const el of stale) {
      const scope = scopeOf(el);
      if (!scope) continue;
      if (!scopes.has(scope)) scopes.set(scope, []);
      scopes.get(scope).push(el);
    }
    for (const [scope, oldElements] of scopes) {
      const replacements = new Map();
      for (const el of scope.querySelectorAll(leaf+'[data-stale="false"]')) {
        if (scopeOf(el) !== scope) continue;
        const id = identity(el);
        if (!id || previous.get(el) === id) continue;
        if (el.checkVisibility?.({visibilityProperty:true}) ?? el.getClientRects().length) {
          replacements.set(id, (replacements.get(id) || 0) + 1);
        }
      }
      for (const el of oldElements) {
        if (el.matches(':focus-within')) continue;
        const id = identity(el);
        const available = replacements.get(id);
        if (available) {
          next.add(el);
          replacements.set(id, available - 1);
        }
      }
    }
    for (const el of marked) if (!next.has(el)) { el.removeAttribute(retired); marked.delete(el); }
    for (const el of next) if (!marked.has(el)) { el.setAttribute(retired, ''); marked.add(el); }
    if (!stale.length && document.querySelector('[data-testid="stApp"]')?.dataset.testScriptState === 'notRunning') {
      for (const el of document.querySelectorAll(leaf)) previous.set(el, identity(el));
    }
  }
  function schedule() {
    if (!pending) { pending = true; queueMicrotask(reconcile); }
  }
  function relevant(change) {
    if (change.type === 'attributes') return true;
    const parent = change.target.parentElement;
    if (parent?.closest('.js-plotly-plot,[data-testid="stDataFrame"]')) return false;
    // A text-only update may reuse an existing leaf.
    if (parent?.closest(leaf)) return hasStale;
    for (const node of [...change.addedNodes, ...change.removedNodes]) {
      if (node.nodeType === 1 && (node.matches(leaf) || node.querySelector(leaf))) return true;
    }
    return false;
  }
  new MutationObserver(changes => {
    if (changes.some(relevant)) schedule();
  }).observe(document.body, {subtree:true, childList:true, characterData:true, attributes:true,
    attributeFilter:['data-stale','data-test-script-state']});
  schedule();
})();
</script>
"""
