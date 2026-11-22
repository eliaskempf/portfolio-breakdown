"""Identical frame sampling in WebView2, Qt, WKWebView and the browser."""
import hashlib
import time

CONTEXT = """const w = document.querySelector('iframe#portfolio-app')?.contentWindow || window;
const d = w.document;
const visible = e => e?.getBoundingClientRect().width > 0 && !e.closest('[data-stale=true]');
const history = () => [...d.querySelectorAll('.js-plotly-plot')].find(e =>
    e.data?.some(t => t.name === 'Value') && e.querySelector('.scatterlayer .js-line'));
const running = () => d.querySelector('[data-testid=stApp]')?.getAttribute('data-test-script-state') !== 'notRunning';
const allocation = () => d.querySelector('.st-key-overview_allocation .js-plotly-plot .main-svg path.surface');
const exception = () => !!d.querySelector('[data-testid=stException]');
"""


def wait(read, timeout=60):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = read()
        if value:
            return value
        time.sleep(.05)
    raise TimeoutError('Timed out waiting for the benchmark window')


def interact(evaluate, command):
    import json
    evaluate('(() => {' + CONTEXT + 'const label=' + json.dumps(command) + """;
    const control = [...d.querySelectorAll('[role=radio],button')].find(e => visible(e) && e.textContent.trim() === label);
    if (!control) throw Error('Missing control ' + label);
    const start = w.performance.now(), epoch = w.performance.timeOrigin + start;
    const previous = history()?.data[0]?.x[0];
    w.benchmarkResult = null;
    control.click();
    let first = null, confirmations = 0;
    function frame() {
        const elapsed = w.performance.now() - start, chart = history();
        const target = label === 'Performance' ? visible(chart) :
            label === 'Allocation' ? visible(allocation()) : visible(chart) && chart.data[0].x[0] !== previous;
        if (target && first === null) first = elapsed;
        const settled = target && !running() && (label !== 'Performance' ||
            visible(d.querySelector('.st-key-tour_performance .js-plotly-plot .main-svg')));
        confirmations = settled ? confirmations + 1 : 0;
        if (confirmations >= 2) w.benchmarkResult = {chart_ms:first, settled_ms:elapsed, click_epoch_ms:epoch};
        else if (exception() || elapsed > 30000) w.benchmarkResult = {error:'View failed to settle: ' + label};
        else w.requestAnimationFrame(frame);
    }
    w.requestAnimationFrame(frame);
    return true; })()""")
    result = wait(lambda: evaluate('(() => {' + CONTEXT + 'return w.benchmarkResult;})()'))
    if 'error' in result:
        raise RuntimeError(result['error'])
    return result


def exercise(evaluate, control, scenario, *, persistent):
    ready = '(!document.querySelector("#portfolio-app") || window.PortfolioSplash?.state === "first-view-rendered")'
    animation_done = None
    def is_ready():
        nonlocal animation_done
        try:
            state = evaluate('(() => {' + CONTEXT + 'return {intro:!!window.BreakdownIntro?.completed, ready:' +
                ready + ' && visible(allocation()) && !running()};})()')
            if state['intro'] and animation_done is None:
                animation_done = time.time()
            return state['ready']
        except Exception:
            return False  # The native startup document is replaced on Qt and Cocoa.
    wait(is_ready, timeout=100)
    ready_at = time.time()
    first = interact(evaluate, 'Performance')
    wait(lambda: (control / 'pending').exists())
    period = interact(evaluate, '3M')
    if (control / 'completed').exists():
        raise AssertionError('The date range must be usable during the blocked download')
    evaluate('(() => {' + CONTEXT + """
        const original = history(), closing = original.data[0].y.at(-1), firstDay = original.data[0].x[0];
        let faded = false, last = closing;
        const state = w.benchmarkRefresh = {changes:0, fades:0, replaced:false, preserved:true, delta:0};
        function frame() {
            const chart = history();
            if (chart) {
                if (chart !== original) state.replaced = true;
                const stale = !!chart.closest('[data-stale=true]');
                if (stale && !faded) state.fades++;
                faded = stale;
                const value = chart.data[0].y.at(-1);
                if (value !== last) { state.changes++; last = value; }
                state.delta = value - closing;
                if (chart.data[0].x[0] !== firstDay) state.preserved = false;
            } else {
                state.replaced = true;
            }
            if (!state.done) w.requestAnimationFrame(frame);
        }
        frame(); return true; })()""")
    refresh_started = time.perf_counter()
    (control / 'release').touch()
    wait(lambda: (control / 'completed').exists())
    held = 0
    if persistent and scenario == 'changed':
        wait(lambda: (control / 'recalculating').exists(), timeout=30)
        if not (control / 'contended').exists():
            raise AssertionError('Background preparation did not retry the busy queue')
        before = time.perf_counter()
        try:
            time.sleep(.4)
            stable = evaluate('(() => {' + CONTEXT + 'const s=w.benchmarkRefresh; return visible(history()) && !s.replaced && !s.fades && s.changes===0;})()')
            if not stable:
                raise AssertionError('Last complete chart disappeared during recalculation')
        finally:
            (control / 'finish-calc').touch()
            held = time.perf_counter() - before
    wait(lambda: evaluate('(() => {' + CONTEXT + """
        return !exception() && !running() && !d.body.innerText.includes('Loading historical prices and FX');
    })()"""))
    if scenario == 'changed':
        wait(lambda: evaluate('(() => {' + CONTEXT + 'return w.benchmarkRefresh?.delta === 25 && !running();})()'))
    refresh_ms = (time.perf_counter() - refresh_started - held) * 1000
    time.sleep(5)  # Observe more than two status-timer periods after completion.
    refresh = evaluate('(() => {' + CONTEXT + 'w.benchmarkRefresh.done=true; return w.benchmarkRefresh;})()')
    if not refresh['preserved'] or refresh['changes'] != int(scenario == 'changed') or refresh['delta'] != (25 if scenario == 'changed' else 0):
        raise AssertionError(f'Unexpected refresh result {refresh}')
    if scenario == 'unchanged' and (refresh['fades'] or refresh['replaced']):
        raise AssertionError(f'Unchanged refresh disturbed chart {refresh}')
    series = evaluate('(() => {' + CONTEXT + 'return JSON.stringify(history().data.map(t => [t.name,t.x,Array.from(t.y),t.customdata]));})()')
    digest = hashlib.sha256(series.encode()).hexdigest()
    interact(evaluate, '1Y')
    warm = []
    for _ in range(5):
        interact(evaluate, 'Allocation')
        warm.append(interact(evaluate, 'Performance'))
    interact(evaluate, 'Allocation')  # Restore saved view for the next real process.
    return dict(digest=digest, ready_at=ready_at, post_intro_ms=(ready_at-animation_done)*1000 if animation_done else None,
                refresh_ms=refresh_ms, first=first, range_3m=period, warm=warm, refresh=refresh)
