"""Keep Streamlit's portalled cell editors anchored to their source tables.

Glide retains control of validation, commits and cancellation. This adapter only
updates screen coordinates on page scroll and opens the next text/number editor
after Glide's normal Tab navigation. No portfolio data is passed to JavaScript.
"""

import streamlit as st

SCRIPT = r"""
<script>
(() => {
  const key = '__portfolioGridInteractions';
  window[key]?.abort();
  const controller = new AbortController();
  window[key] = controller;
  const options = {capture: true, signal: controller.signal};
  const overlaySelector = '[id^="gdg-overlay-"]';
  const anchors = new WeakMap();
  let scheduled = false;
  let tabGrid = null;
  let tabTimeout;
  let nextCellId = null;
  let openingCell = null;

  function pinEditors() {
    if (controller.signal.aborted) return;
    for (const editor of document.querySelectorAll(overlaySelector)) {
      let anchor = anchors.get(editor);
      if (!anchor) {
        const style = getComputedStyle(editor);
        const left = parseFloat(style.left), top = parseFloat(style.top);
        if (!Number.isFinite(left) || !Number.isFinite(top)) continue;
        const grid = [...document.querySelectorAll('[data-testid="stDataFrame"]')].find(node => {
          const bounds = node.getBoundingClientRect();
          return bounds.width > 0 && bounds.height > 0 && left + 2 >= bounds.left &&
            left < bounds.right && top + 2 >= bounds.top && top < bounds.bottom;
        });
        if (!grid) continue;
        const bounds = grid.getBoundingClientRect();
        const scroller = grid.querySelector('.dvn-scroller');
        anchor = {grid, scroller, cell: openingCell, x: left - bounds.left + (scroller?.scrollLeft || 0),
          y: top - bounds.top + (scroller?.scrollTop || 0), height: editor.getBoundingClientRect().height};
        openingCell = null;
        anchors.set(editor, anchor);
      }
      const bounds = anchor.grid.getBoundingClientRect();
      const left = bounds.left + anchor.x - (anchor.scroller?.scrollLeft || 0);
      const top = bounds.top + anchor.y - (anchor.scroller?.scrollTop || 0);
      editor.style.left = `${left}px`;
      editor.style.top = `${top}px`;
      const main = anchor.grid.closest('[data-testid="stMain"]')?.getBoundingClientRect();
      const header = document.querySelector('[data-testid="stHeader"]')?.getBoundingClientRect();
      const visibleTop = Math.max(main?.top || 0, header?.bottom || 0);
      const visibleBottom = Math.min(main?.bottom || window.innerHeight, window.innerHeight);
      const visible = anchor.grid.isConnected && bounds.width > 0 && bounds.height > 0 &&
        top + anchor.height > Math.max(visibleTop, bounds.top) &&
        top < Math.min(visibleBottom, bounds.bottom) && left < bounds.right &&
        left + editor.offsetWidth > bounds.left;
      // Keep the edit alive when its cell leaves the viewport, but do not let
      // the portal cover unrelated controls. Scrolling back restores it.
      // Opacity preserves focus and selection when the cell leaves the viewport.
      editor.style.opacity = visible ? '' : '0';
      editor.style.pointerEvents = visible ? '' : 'none';
      editor.style.clipPath = visible
        ? `inset(${Math.max(0, visibleTop - top)}px 0 ${Math.max(0, top + editor.offsetHeight - visibleBottom)}px 0)`
        : '';
    }
  }
  function schedulePin() {
    if (scheduled) return;
    scheduled = true;
    requestAnimationFrame(() => { scheduled = false; pinEditors(); });
  }
  const observer = new MutationObserver(schedulePin);
  observer.observe(document.body, {childList: true, subtree: true});
  controller.signal.addEventListener('abort', () => {
    observer.disconnect();
    clearTimeout(tabTimeout);
  }, {once: true});
  document.addEventListener('scroll', pinEditors, options);
  window.addEventListener('resize', pinEditors, {signal: controller.signal});
  pinEditors();

  document.addEventListener('keydown', event => {
    tabGrid = null;
    clearTimeout(tabTimeout);
    const editor = event.target.closest?.(overlaySelector);
    // Glide schedules a deferred finish even for modifier-only keydowns. A
    // fast Shift+Tab can otherwise run Shift's cancellation before Tab's save.
    // Preserve the modifier state/default action, but keep that event local.
    if (editor && ['Shift', 'Control', 'Alt', 'Meta'].includes(event.key)) event.stopPropagation();
    if (event.key !== 'Tab' || event.isComposing || !editor) return;
    pinEditors();
    const anchor = anchors.get(editor);
    tabGrid = anchor?.grid;
    const origin = anchor?.cell || tabGrid?.querySelector('[role="gridcell"][aria-selected="true"]');
    const coordinates = origin?.id.match(/^glide-cell-(\d+)-(\d+)$/);
    nextCellId = coordinates
      ? `glide-cell-${Number(coordinates[1]) + (event.shiftKey ? -1 : 1)}-${coordinates[2]}` : null;
    tabTimeout = setTimeout(() => { tabGrid = null; }, 1000);
  }, options);
  document.addEventListener('pointerdown', () => { tabGrid = null; openingCell = null; }, options);
  document.addEventListener('focusin', event => {
    const cell = event.target;
    if (!tabGrid || !cell.matches?.('[role="gridcell"]') || !tabGrid.contains(cell)) return;
    if (nextCellId && cell.id !== nextCellId) return;
    // Glide commits and moves first. Its accessibility tree updates after a
    // debounce, so wait for actual cell focus instead of guessing the delay.
    requestAnimationFrame(() => {
      if (controller.signal.aborted || !tabGrid?.contains(cell) || document.activeElement !== cell ||
          document.querySelector(overlaySelector) || cell.getAttribute('aria-readonly') !== 'false') return;
      tabGrid = null;
      // Enter toggles booleans: leave those selected for a deliberate keypress.
      if (['true', 'false'].includes(cell.textContent.trim())) return;
      openingCell = cell;
      cell.dispatchEvent(new KeyboardEvent('keydown', {
        key: 'Enter', code: 'Enter', keyCode: 13, which: 13, bubbles: true,
      }));
    });
  }, options);
})();
</script>
"""


def install_grid_interactions() -> None:
    st.html(SCRIPT, unsafe_allow_javascript=True)
