"""Browser-local appearance controls using Streamlit's per-viewer theme engine."""
import streamlit as st


def appearance_controls() -> None:
    st.html("""
    <style>
    .portfolio-appearance {display:flex;align-items:center;flex-wrap:wrap;gap:.5rem;color:inherit}
    .portfolio-appearance label {display:flex;align-items:center;gap:.5rem}
    .portfolio-appearance select,.portfolio-appearance button {font:inherit;color:inherit;
        background:var(--st-secondary-background-color);border:1px solid var(--st-border-color);
        border-radius:.25rem;padding:.25rem .5rem;min-height:2rem}
    .portfolio-appearance :focus-visible {outline:2px solid var(--st-primary-color);outline-offset:2px}
    @media print {[data-testid="stPopoverBody"] {display:none !important}}
    </style>
    <div class="portfolio-appearance" id="portfolio-appearance">
      <label>Appearance <select><option>System</option><option>Light</option><option>Dark</option></select></label>
      <button type="button">Print view</button><span role="status"></span>
    </div>
    <script>
    (() => {
      const controls = document.getElementById('portfolio-appearance');
      const select = controls.querySelector('select');
      const status = controls.querySelector('[role=status]');
      const storageKey = 'stActiveTheme-' + location.pathname + '-v2';
      let preference = 'System';
      try { preference = JSON.parse(localStorage.getItem(storageKey)) || preference; } catch {}
      select.value = preference;
      controls.querySelector('button').onclick = () => window.print();
      select.onchange = async () => {
        const desired = select.value;
        const trigger = document.querySelector('[data-testid="stMainMenuButton"]');
        // React delegates these native Streamlit controls. Keep only our synthetic
        // clicks from reaching the document's outside-click handler for App menu.
        const keepMenu = event => {
          if (!event.isTrusted && event.target.closest('[data-testid="stMainMenu"],[data-testid="stMainMenuPopover"]'))
            event.stopPropagation();
        };
        const frame = () => new Promise(requestAnimationFrame);
        document.body.addEventListener('click', keepMenu);
        try {
          if (trigger?.getAttribute('aria-expanded') !== 'true') trigger?.click();
          await frame();
          const item = document.querySelector('[data-testid="stMainMenuItem-theme-' + desired + '"]');
          if (!item) throw Error();
          item.click();
          preference = desired;
          status.textContent = '';
        } catch {
          select.value = preference;
          status.textContent = 'Appearance could not be changed.';
        } finally {
          await frame();
          if (trigger?.getAttribute('aria-expanded') === 'true') trigger.click();
          document.body.removeEventListener('click', keepMenu);
          select.focus({preventScroll:true});
        }
      };
    })();
    </script>
    """, unsafe_allow_javascript=True)
