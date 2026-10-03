"""Self-contained startup surface; the app renders beneath it at full size."""
from pathlib import Path


VIEW_PROBE = """(() => {
 const doc = document;
 if (doc.querySelector('[data-testid="stException"]')) return 'failed';
 const marker = doc.querySelector('[data-portfolio-view-ready="true"]');
 const welcome = [...doc.querySelectorAll('h1,h2,h3')].some(
   e => e.textContent === 'Welcome to Portfolio Breakdown');
 const overview = doc.querySelector('[role="tab"][aria-selected="true"]');
 const charts = [...doc.querySelectorAll('.js-plotly-plot')];
 const drawn = charts.length && charts.every(e => e.querySelector('.main-svg'));
 return (marker && (!charts.length || drawn)) || welcome || (overview?.textContent === 'Overview' && drawn)
   ? 'first-view-rendered' : 'document-loaded';
})()"""


def startup_html(show_intro=True):
    html = (Path(__file__).with_name('intro_frontend') / 'index.html').read_text(encoding='utf-8')
    style = '''<style>
    html,body{height:100%;overflow:hidden}
    body main{position:fixed;inset:48px 0 0;z-index:2;width:100%;height:calc(100dvh - 48px);margin:0;
      display:flex;align-items:center;justify-content:center;background:#11151c;
      transition:opacity 250ms ease}
    header,.controls,footer{display:none}
    .stage{width:100%;border:0;border-radius:0}
    #scene{width:100%;height:min(360px,100dvh)}
    #portfolio-app{position:fixed;inset:48px 0 0;width:100%;height:calc(100% - 48px);border:0}
    .window-controls{position:fixed;inset:0 0 auto;height:48px;z-index:3;display:flex;
      align-items:center;justify-content:flex-end;gap:8px;padding:6px 16px;background:#11151c}
    .window-controls button{height:34px;padding:0 12px;border:1px solid #354052;
      border-radius:8px;background:#1b2230;color:#e3e7ef;font:500 14px system-ui;
      display:inline-flex;gap:8px;align-items:center;cursor:pointer;letter-spacing:normal}
    .window-controls button:hover{background:#283244;border-color:#52627b}
    .window-controls svg{width:16px;height:16px;fill:none;stroke:currentColor;stroke-width:1.6}
    .window-controls #window-exit{background:#b82c3b;border-color:#b82c3b;color:white}
    .window-controls #window-exit:hover{background:#ce3545;border-color:#ce3545}
    .window-controls [hidden]{display:none}
    body[data-phase="done"] main:not(.leaving) #scene{animation:loading-pulse 1.6s ease-in-out infinite}
    @keyframes loading-pulse{50%{opacity:.65}}
    main.leaving{opacity:0;pointer-events:none}
    #slow-start{position:absolute;top:calc(50% + 180px);text-align:center;font-size:14px}
    #slow-start[hidden]{display:none}
    @media(prefers-reduced-motion:reduce){
      body[data-phase="done"] main:not(.leaving) #scene{animation:none}body main{transition:none}
    }
    </style>'''
    script = r'''<script>
    (() => {
      const splash = document.querySelector('main');
      const controls = document.createElement('nav');
      controls.className = 'window-controls'; controls.ariaLabel = 'Window controls';
      controls.innerHTML = `<button id="window-mode" title="Toggle fullscreen (F11)">
        <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M8 3H3v5m13-5h5v5M3 16v5h5m13-5v5h-5"/></svg>
        <span>Fullscreen</span></button><button id="window-exit" hidden title="Close Portfolio Breakdown">
        <svg viewBox="0 0 24 24" aria-hidden="true"><path d="m6 6 12 12M18 6 6 18"/></svg>Exit</button>`;
      document.body.append(controls);
      controls.querySelector('#window-mode').onclick = () => window.chrome?.webview?.postMessage('portfolio:fullscreen');
      controls.querySelector('#window-exit').onclick = () => window.chrome?.webview?.postMessage('portfolio:exit');
      const showIntro = SHOW_INTRO;
      if (!showIntro) { window.BreakdownIntro.finish(); splash.remove(); }
      const frame = document.createElement('iframe');
      frame.id = 'portfolio-app'; frame.title = 'Portfolio Breakdown';
      frame.inert = true;
      document.body.prepend(frame);
      const slow = document.createElement('div');
      slow.id = 'slow-start'; slow.hidden = true;
      slow.innerHTML = '<p>Loading is taking longer than expected.</p><button>Show application</button>';
      splash.append(slow);
      let opened = false, leaving = false, readySince = null;
      const started = performance.now();
      let state = 'starting';
      function reveal(next) {
        if (leaving) return;
        leaving = true;
        clearInterval(timer);
        frame.inert = false;
        splash.classList.add('leaving');
        const duration = matchMedia('(prefers-reduced-motion: reduce)').matches ? 0 : 260;
        setTimeout(() => { splash.remove(); frame.focus(); state = next; }, duration);
      }
      slow.querySelector('button').onclick = () => reveal('document-loaded');
      const timer = setInterval(() => {
        const elapsed = performance.now() - started;
        if (elapsed > 60000) slow.hidden = false;
        if (!opened) return;
        let next = 'document-loaded';
        try {
          // Same-origin frame: inspect rendered DOM, never portfolio values.
          next = frame.contentWindow.eval(PROBE);
        } catch (_) { return; }
        if (next === 'failed') { reveal(next); return; }
        if (next !== 'first-view-rendered') { readySince = null; return; }
        readySince ??= performance.now();
        const animationDone = window.BreakdownIntro?.completed || elapsed > 8000;
        // Allow the first batch of charts and the layout to settle before fading.
        if (animationDone && performance.now() - readySince >= 300) reveal(next);
      }, 100);
      window.PortfolioSplash = {
        get state() { return state; },
        setFullscreen(fullscreen) {
          controls.querySelector('#window-mode span').textContent = fullscreen ? 'Windowed' : 'Fullscreen';
          controls.querySelector('#window-exit').hidden = !fullscreen;
        },
        open(url) {
          if (opened) return true;
          if (new URL(url).origin !== location.origin) return false;
          opened = true; state = 'document-loaded'; frame.src = url;
          return true;
        }
      };
    })();
    </script>'''
    import json
    script = script.replace('PROBE', json.dumps(VIEW_PROBE)).replace('SHOW_INTRO', json.dumps(show_intro))
    return html.replace('</head>', style + '</head>').replace('</body>', script + '</body>')
