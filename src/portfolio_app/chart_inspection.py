"""Client-only hover markers and dated history metric readouts."""
import streamlit as st

JS = """
export default function({parentElement}) {
  parentElement.disposeChartInspection?.();
  const plots = new Map();
  const attach = () => {
    for (const [plot, dispose] of plots) {
      if (!plot.isConnected) { dispose(); plots.delete(plot); }
    }
    for (const plot of document.querySelectorAll('.js-plotly-plot')) {
      if (!plot.on || plots.has(plot)) continue;
      const clearMarkers = () => plot.querySelectorAll('.portfolio-hover-point').forEach(p => p.remove());
      const metrics = points => {
        const config = plot.layout?.meta?.historyInspection;
        if (!config || !plot.isConnected) return;
        const number = new Intl.NumberFormat('en-US', {minimumFractionDigits:2, maximumFractionDigits:2});
        const currency = new Intl.NumberFormat('en-US', {style:'currency',currency:config.currency,minimumFractionDigits:2,maximumFractionDigits:2});
        const money = amount => config.symbol_money ? currency.format(amount) : number.format(amount) + ' ' + config.currency;
        const signed = amount => (amount >= 0 ? '+' : '-') + money(Math.abs(amount));
        const date = new Intl.DateTimeFormat('en-GB', {day:'2-digit',month:'short',year:'numeric',timeZone:'UTC'});
        for (const metric of config.metrics) {
          const container = document.querySelector('.st-key-' + CSS.escape(metric.key));
          const label = container?.querySelector('[data-testid="stMetricLabel"] p');
          const value = container?.querySelector('[data-testid="stMetricValue"]')?.firstElementChild;
          if (!label || !value) continue;
          const point = points?.find(p => p.fullData?.name === metric.trace);
          const valid = point && point.y != null && Number.isFinite(Number(point.y));
          const labelText = valid ? metric.label + ' · ' + date.format(new Date(point.x)) : metric.label;
          const valueText = valid ? money(point.y) : points ? '—' : metric.summary;
          if (label.textContent !== labelText) label.textContent = labelText;
          if (value.textContent !== valueText) value.textContent = valueText;
          if ('gainSummary' in metric) {
            const delta = container.querySelector('[data-testid="stMetricDelta"]');
            const text = delta?.querySelector('[data-testid="stMetricDeltaContent"]') ?? delta?.lastElementChild;
            if (text) {
              const raw = valid ? point.customdata?.[metric.gainPercent ? 1 : 0] : null;
              const amount = points ? (raw == null ? null : raw * (metric.gainPercent ? 100 : 1)) : metric.gainAmount;
              const gain = points ? (amount == null ? '—' : metric.gainPercent ?
                (amount > 0 ? '↑ ' : amount < 0 ? '↓ ' : '') + number.format(Math.abs(amount)) + '%' : signed(amount)) : metric.gainSummary;
              if (text.textContent !== gain) text.textContent = gain;
              const tooltip = points ? (metric.gainPercent ? 'TWR · ' : 'Gain · ') + 'All history' + (valid ? ' to ' + date.format(new Date(point.x)) : '') +
                (metric.gainPercent ? ' · Click for amount' : ' · Click for TWR') : metric.gainTooltip;
              if (tooltip) delta.setAttribute('title', tooltip);
              // Keep the native metric styling at rest and whenever the sign
              // matches it. For a sign change, use the component's theme palette.
              const sameSign = amount != null && metric.gainAmount != null &&
                Math.sign(amount) === Math.sign(metric.gainAmount);
              delta.style.removeProperty('color');
              delta.style.removeProperty('background-color');
              if (points && !sameSign) {
                const palette = getComputedStyle(parentElement.host ?? parentElement);
                const color = amount == null || amount === 0 ? 'gray' : amount < 0 ? 'red' : 'green';
                delta.style.color = palette.getPropertyValue('--st-' + color + '-text-color');
                delta.style.backgroundColor = palette.getPropertyValue('--st-' + color + '-background-color');
              }
            }
          }
        }
      };
      const clear = () => { clearMarkers(); metrics(null); };
      const hover = event => {
        clearMarkers();
        if (plot._fullLayout?.hovermode !== 'x unified') return;
        metrics(event.points ?? []);
        const layer = plot.querySelector('.hoverlayer');
        if (!layer) return;
        for (const p of event.points ?? []) {
          if (!['scatter', 'scattergl'].includes(p.fullData?.type) || p.y == null) continue;
          const x = p.xaxis.d2p(p.x) + p.xaxis._offset;
          const y = p.yaxis.d2p(p.y) + p.yaxis._offset;
          if (!Number.isFinite(x) || !Number.isFinite(y)) continue;
          const dot = document.createElementNS('http://www.w3.org/2000/svg', 'circle');
          const color = p.fullData.line?.color ?? p.fullData.marker?.color ?? '#87919f';
          for (const [name, value] of Object.entries({class:'portfolio-hover-point',cx:x,cy:y,r:4,
            fill:color,stroke:'#fff','stroke-width':1.5,'pointer-events':'none'})) dot.setAttribute(name,value);
          layer.insertBefore(dot,layer.firstChild);
        }
      };
      plot.on('plotly_hover', hover);
      plot.on('plotly_unhover', clear);
      plot.on('plotly_afterplot', clear);
      plot.addEventListener('mouseleave', clear);
      metrics(null);
      plots.set(plot, () => {
        plot.removeListener?.('plotly_hover', hover);
        plot.removeListener?.('plotly_unhover', clear);
        plot.removeListener?.('plotly_afterplot', clear);
        plot.removeEventListener('mouseleave', clear);
        clear();
      });
    }
  };
  const observer = new MutationObserver(attach);
  observer.observe(document.body, {childList:true,subtree:true});
  attach();
  const dispose = () => { observer.disconnect(); for (const cleanup of plots.values()) cleanup(); plots.clear(); };
  parentElement.disposeChartInspection = dispose;
  return dispose;
}
"""


def install_chart_inspection():
    st.components.v2.component('portfolio_chart_inspection', js=JS)(key='chart_inspection')
