"""Browser spotlight geometry and theme-aware presentation for the guided tour."""

JS = r'''
export default function({parentElement, data, setTriggerValue}) {
  // Dispose the previous render before installing listeners or a new mask.
  window.__portfolioTourDispose?.();
  const ns = 'http://www.w3.org/2000/svg';
  const svg = document.createElementNS(ns, 'svg');
  svg.classList.add('portfolio-tour-overlay');
  svg.setAttribute('aria-hidden', 'true');
  const make = (tag, attrs, owner) => {
    const el = document.createElementNS(ns, tag);
    for (const [key, value] of Object.entries(attrs)) el.setAttribute(key, value);
    owner.append(el);
    return el;
  };
  const defs = make('defs', {}, svg);
  const mask = make('mask', {id:'portfolio-tour-mask', maskUnits:'userSpaceOnUse', x:0, y:0}, defs);
  const base = make('rect', {fill:'white'}, mask);
  const shade = make('rect', {fill:'#0a1020', 'fill-opacity':'.65', mask:'url(#portfolio-tour-mask)', 'pointer-events':'none'}, svg);
  // Keep visual grouping separate from hit testing: only the explicitly
  // interactive chart receives pointer events through the overlay.
  const blocker = make('path', {fill:'transparent', 'fill-rule':'evenodd', 'pointer-events':'fill'}, svg);
  document.body.append(svg);
  let card, frame, timer, disposed = false, scrolled = false, focused = false;
  let holes = [], borders = [], accents = [];
  const targets = () => data.anchors.map(s => document.querySelector(s)).filter(e => e?.getBoundingClientRect().height);
  const schedule = () => { if (!disposed && !frame) frame = requestAnimationFrame(draw); };
  const draw = () => {
    frame = null;
    card = document.querySelector('.st-key-app_tour');
    if (!card) return;
    // Read the rendered app theme so explicit menu choices and system-theme
    // changes both update the card without relying on an OS-only media query.
    const appStyle = getComputedStyle(document.querySelector('[data-testid="stApp"]'));
    const rgb = appStyle.backgroundColor.match(/[\d.]+/g)?.slice(0,3).map(Number) || [255,255,255];
    const dark = (.2126*rgb[0] + .7152*rgb[1] + .0722*rgb[2]) < 128;
    const palette = dark
      ? {surface:'#1a202b', text:'#e3e7ef', body:'#e0e7f5', muted:'#afbfed', border:'#647eca', button:'#243550', 'button-border':'#647595'}
      : {surface:'#ffffff', text:'#202632', body:'#384354', muted:'#4c5c88', border:'#a4b3de', button:'#f3f5fb', 'button-border':'#b9c4df'};
    for (const [name,value] of Object.entries(palette)) card.style.setProperty(`--tour-${name}`, value);
    card.dataset.tourTheme = dark ? 'dark' : 'light';
    shade.setAttribute('fill-opacity', dark ? '.65' : '.45');
    const w = innerWidth, h = innerHeight, margin = 12;
    const elements = targets();
    if (elements.length !== data.anchors.length || document.querySelector('[data-test-script-state="running"]')) {
      timer = setTimeout(schedule, 80);
      return;
    }
    card.setAttribute('role', 'dialog');
    card.setAttribute('aria-modal', data.interactive ? 'false' : 'true');
    card.setAttribute('aria-label', 'Guided demo tour');
    if (!focused) {
      (card.querySelector('.st-key-tour_next button, .st-key-tour_finish button') || card.querySelector('button'))?.focus({preventScroll:true});
      focused = true;
    }
    if (!scrolled) {
      const first = elements[0].getBoundingClientRect();
      const last = elements.at(-1).getBoundingClientRect();
      const scroll = document.querySelector('[data-testid="stMain"]');
      if (scroll && (first.top < 75 || last.bottom > h - card.offsetHeight - 36)) {
        scroll.scrollBy({top:first.top - 85, behavior:'instant'});
      }
      scrolled = true;
    }
    const rects = elements.map(e => e.getBoundingClientRect());
    const bounds = {left:Math.min(...rects.map(r=>r.left)), right:Math.max(...rects.map(r=>r.right)),
                    top:Math.min(...rects.map(r=>r.top)), bottom:Math.max(...rects.map(r=>r.bottom))};
    const cw = card.offsetWidth, ch = card.offsetHeight;
    let left = Math.max(margin, w - cw - 24), top = h - ch - margin;
    if (w > 700) {
      if (bounds.right + cw + 32 < w) {
        left = bounds.right + 20; top = Math.max(75, Math.min(bounds.top, h-ch-margin));
      } else if (bounds.bottom + ch + 28 < h) {
        top = bounds.bottom + 20;
      } else if (bounds.top - ch - 20 > 65) {
        top = bounds.top - ch - 20;
      }
    }
    card.style.left = `${left}px`; card.style.top = `${Math.max(margin,top)}px`;
    card.style.right = 'auto'; card.style.bottom = 'auto';
    for (const el of [svg, mask, base, shade]) {
      el.setAttribute('width', w); el.setAttribute('height', h);
    }
    holes.forEach(e=>e.remove()); borders.forEach(e=>e.remove());
    accents.forEach(e=>e.classList.remove('portfolio-tour-accent'));
    holes = []; borders = []; accents = [];
    // Related controls/results share one region; distant controls can retain
    // their own spotlight instead of undimming unrelated content between them.
    data.groups.forEach(group => {
      const rs = group.map(selector => elements[data.anchors.indexOf(selector)].getBoundingClientRect());
      const x = Math.max(4,Math.min(...rs.map(r=>r.left))-6);
      const y = Math.max(4,Math.min(...rs.map(r=>r.top))-6);
      const right = Math.min(w-4,Math.max(...rs.map(r=>r.right))+6);
      const bottom = Math.min(h-4,Math.max(...rs.map(r=>r.bottom))+6);
      if (bottom <= y || right <= x) return;
      const box = {x, y, width:right-x, height:bottom-y, rx:9};
      holes.push(make('rect', {...box, fill:'black', 'data-tour-target':group.join(',')}, mask));
      borders.push(make('rect', {...box, fill:'none', stroke:'#728df0', 'stroke-width':2.5, 'pointer-events':'none', 'data-tour-border':''}, svg));
    });
    elements.forEach(element => {
      element.querySelectorAll('[role="tab"][aria-selected="true"], [role="radio"][aria-checked="true"]').forEach(el => {
        el.classList.add('portfolio-tour-accent'); accents.push(el);
      });
    });
    let hitArea = `M0,0 H${w} V${h} H0 Z`;
    const interactive = data.interactive && document.querySelector(data.interactive);
    if (interactive) {
      const r = interactive.getBoundingClientRect();
      hitArea += ` M${r.left},${r.top} H${r.right} V${r.bottom} H${r.left} Z`;
    }
    blocker.setAttribute('d', hitArea);
    svg.dataset.ready = 'true';
    svg.dataset.step = String(data.step);
  };
  const wheel = event => {
    document.querySelector('[data-testid="stMain"]')?.scrollBy({top:event.deltaY, left:event.deltaX});
    event.preventDefault();
  };
  let touchY;
  const touchStart = event => { touchY = event.touches[0]?.clientY; };
  const touchMove = event => {
    const y = event.touches[0]?.clientY;
    if (y !== undefined && touchY !== undefined) {
      document.querySelector('[data-testid="stMain"]')?.scrollBy({top:touchY-y});
      touchY = y; event.preventDefault();
    }
  };
  svg.addEventListener('wheel', wheel, {passive:false});
  svg.addEventListener('touchstart', touchStart, {passive:true});
  svg.addEventListener('touchmove', touchMove, {passive:false});
  const keyboard = event => {
    if (event.key === 'Escape') {
      event.preventDefault(); event.stopImmediatePropagation();
      setTriggerValue('exit', {step:data.step});
    } else if (event.key === 'Tab' && card) {
      const buttons = [...card.querySelectorAll('button:not(:disabled)')];
      const index = buttons.indexOf(document.activeElement);
      event.preventDefault();
      buttons[(index + (event.shiftKey ? -1 : 1) + buttons.length) % buttons.length]?.focus();
    }
  };
  // Observe app rendering only: observing the SVG itself would cause a redraw loop.
  const observer = new MutationObserver(schedule);
  const app = document.querySelector('[data-testid="stApp"]');
  if (app) observer.observe(app, {childList:true, subtree:true});
  const themeObserver = new MutationObserver(schedule);
  if (app) themeObserver.observe(app, {attributes:true, attributeFilter:['class','style']});
  const resize = new ResizeObserver(schedule);
  targets().forEach(el=>resize.observe(el));
  const initialCard = document.querySelector('.st-key-app_tour');
  if (initialCard) resize.observe(initialCard);
  window.addEventListener('resize', schedule);
  document.addEventListener('scroll', schedule, true);
  document.addEventListener('keydown', keyboard, true);
  schedule();
  const dispose = () => {
    disposed = true; cancelAnimationFrame(frame); clearTimeout(timer);
    observer.disconnect(); themeObserver.disconnect(); resize.disconnect(); svg.remove();
    accents.forEach(e=>e.classList.remove('portfolio-tour-accent'));
    window.removeEventListener('resize', schedule);
    document.removeEventListener('scroll', schedule, true);
    document.removeEventListener('keydown', keyboard, true);
    requestAnimationFrame(() => {
      if (!document.querySelector('.st-key-app_tour')) document.querySelector('.st-key-main_tabs [aria-selected="true"]')?.focus({preventScroll:true});
    });
    if (window.__portfolioTourDispose === dispose) delete window.__portfolioTourDispose;
  };
  window.__portfolioTourDispose = dispose;
  return dispose;
}
'''
