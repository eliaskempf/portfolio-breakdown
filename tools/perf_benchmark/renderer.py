"""Identical frame sampling in WebView2, Qt, WKWebView and the browser."""
import hashlib
import json
import subprocess
import sys
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


def event_count(trial):
    # Both the native window and external Chromium driver see this trial's server log.
    return (trial / 'server.jsonl').read_bytes().count(b'\n')


def interact(evaluate, command, control):
    first_event = event_count(control)
    dispatched = time.monotonic_ns() / 1e6
    evaluate('(() => {' + CONTEXT + 'const label=' + json.dumps(command) + """;
    const control = [...d.querySelectorAll('[role=radio],button')].find(e => visible(e) && e.textContent.trim() === label);
    if (!control) throw Error('Missing control ' + label);
    const start = w.performance.now();
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
        if (confirmations >= 2) w.benchmarkResult = {chart_ms:first, settled_ms:elapsed};
        else if (exception() || elapsed > 30000) {
            const active = [...d.querySelectorAll('[role=radio][aria-checked=true]')].map(e => e.textContent.trim());
            w.benchmarkResult = {error:'View failed to settle: ' + JSON.stringify({
                label, active, history:!!visible(chart), allocation:!!visible(allocation()), running:running(),
                exception:exception()})};
        }
        else w.requestAnimationFrame(frame);
    }
    w.requestAnimationFrame(frame);
    return true; })()""")
    result = wait(lambda: evaluate('(() => {' + CONTEXT + 'return w.benchmarkResult;})()'))
    if 'error' in result:
        raise RuntimeError(result['error'])
    result.update(event_range=[first_event, event_count(control)], dispatch_ms=dispatched)
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
    background = {}
    if scenario == 'quote-pending':
        from portfolio_app.interface.shared.money import format_money
        quote = json.loads((control.parent / 'quote-check.json').read_bytes())
        read_total = '(() => {' + CONTEXT + """
            const metric = d.querySelector('.st-key-overview_value [data-testid=stMetricValue]');
            return visible(metric) && metric.textContent.trim();
        })()"""
        requests = quote['requests']
        wait(lambda: all((control / f'quote-pending-{i}').exists() for i in range(len(requests))))
        def release_quote(index):
            (control / f'release-quote-{index}').touch()
            wait(lambda: (control / f'quote-fetched-{index}').exists())
        def saved_quotes_match(field):
            path = control.parent / 'synthetic' / '.cache' / 'prices.json'
            saved = json.loads(path.read_bytes())
            return all(saved[r['key']]['quote']['price'] == r[field] for r in requests)
        initial = format_money(quote['initial_total'], quote['currency'])
        if wait(lambda: evaluate(read_total)) != initial:
            raise AssertionError(f'Expected cached portfolio value {initial}')
        background['start_event'] = event_count(control)
        release_quote(0)
    first = interact(evaluate, 'Performance', control)
    wait(lambda: (control / 'pending').exists())
    if scenario == 'quote-pending':
        interact(evaluate, 'Allocation', control)
        displayed = wait(lambda: evaluate(read_total))
        if displayed != initial:
            raise AssertionError(f'Navigation displayed {displayed} while the complete cached total was {initial}')
        release_quote(2)
        interact(evaluate, 'Performance', control)
    period = interact(evaluate, '3M', control)
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
    if scenario.startswith('etf-'):
        bell = CONTEXT + """
            const bell = [...d.querySelectorAll('.st-key-header_notifications [data-testid=stPopoverButton]')]
                .find(button => button.textContent.includes('Notifications'));
            if (!bell) throw Error('Notification bell is missing');
        """
        def bell_action(js):
            return evaluate('(() => {' + bell + js + '})()')
        def read_badge():
            return bell_action('return Number(w.getComputedStyle(bell,"::after").content.replace(/"/g,"")) || 0;')
        opened = scenario == 'etf-open-failed'
        wait(lambda: (control / 'etf-pending').exists())
        background['badge_before'] = read_badge()
        expected = 'failed' if scenario.endswith('-failed') else 'checked'
        status_path = control.parent / 'synthetic' / '.cache' / 'etf-refresh' / 'status.json'
        background['start_event'] = event_count(control)
        if opened:
            bell_action('bell.click(); return true;')
            wait(lambda: bell_action('return d.body.innerText.includes("Discovering and updating ETF holdings");'))
            if read_badge():
                raise AssertionError('Opening Notifications did not mark the displayed notices read')
        (control / 'release-etf').touch()
        wait(lambda: json.loads(status_path.read_bytes()).get('ZZF000000000', {}).get('status') == expected)
        if opened:
            wait(lambda: bell_action('return d.body.innerText.includes("1 refresh(es) failed; saved data retained")'
                                     ' && !d.body.innerText.includes("Discovering and updating ETF holdings");'))
            bell_action('bell.click(); return true;')
            wait(lambda: bell_action('return !d.body.innerText.includes("Review ETF sources");'))
        elif expected == 'failed':
            wait(lambda: read_badge() == background['badge_before'] + 1)
        # Observe ETF completion on its own before releasing historical prices.
        time.sleep(4.5)
        background['badge_after'] = read_badge()
        expected_badge = 0 if opened else background['badge_before'] + int(expected == 'failed')
        if background['badge_after'] != expected_badge:
            raise AssertionError(f'Incorrect ETF unread badge: {background}')
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
    if scenario != 'changed' and (refresh['fades'] or refresh['replaced']):
        raise AssertionError(f'Unchanged refresh disturbed chart {refresh}')
    if scenario.startswith('etf-'):
        background['end_event'] = event_count(control)
    series = evaluate('(() => {' + CONTEXT + 'return JSON.stringify(history().data.map(t => [t.name,t.x,Array.from(t.y),t.customdata]));})()')
    digest = hashlib.sha256(series.encode()).hexdigest()
    interact(evaluate, '1Y', control)
    warm = []
    for index in range(5):
        allocation = interact(evaluate, 'Allocation', control)
        if index == 0 and scenario.endswith('-failed'):
            background['post_notice_navigation'] = allocation
        warm.append(interact(evaluate, 'Performance', control))
    interact(evaluate, 'Allocation', control)  # Restore saved view for the next real process.
    if scenario == 'quote-pending':
        if wait(lambda: evaluate(read_total)) != initial:
            raise AssertionError('Cached value changed during navigation')
        background['end_event'] = event_count(control)
        for label in ('Positions', 'Overview'):
            tab = ('(() => {' + CONTEXT + 'const label = ' + json.dumps(label) +
                   '; const tab = [...d.querySelectorAll("[role=tab]")].find(t => t.textContent.trim() === label);')
            evaluate(tab + 'tab.click(); return true;})()')
            target = "d.querySelector('.st-key-tour_position_list')" if label == 'Positions' else 'allocation()'
            wait(lambda: evaluate(tab + "return tab?.getAttribute('aria-selected') === 'true' && visible(" +
                                  target + ") && !running() && !exception();})()"))
        if wait(lambda: evaluate(read_total)) != initial:
            raise AssertionError('Switching tabs exposed a partially updated portfolio')
        if not saved_quotes_match('initial'):
            raise AssertionError('A relaunch would load a partial quote refresh')
        background['publication_start_event'] = event_count(control)
        release_quote(1)
        corrected = format_money(quote['corrected_total'], quote['currency'])
        wait(lambda: evaluate(read_total) == corrected and evaluate('(() => {' + CONTEXT + 'return !running();})()'))
        if not saved_quotes_match('corrected'):
            raise AssertionError('The completed portfolio refresh was missing from the installed cache')
        background.update(initial_total=initial, corrected_total=corrected)
    return dict(digest=digest, ready_at=ready_at, post_intro_ms=(ready_at-animation_done)*1000 if animation_done else None,
                refresh_ms=refresh_ms, first=first, range_3m=period, warm=warm, refresh=refresh, **background)


def exercise_fundamentals(evaluate, trial, variant):
    def read(js):
        return evaluate('(() => {' + CONTEXT + js + '})()')

    first_event = event_count(trial)
    read("""
        const button = [...d.querySelectorAll('[role=radio],button')].find(e =>
            visible(e) && e.textContent.trim() === 'Analytics');
        const start = w.performance.now();
        w.fundamentalVisit = null;
        w.fundamentalValues = [];
        button.click();
        let drawn = null;
        function frame() {
            const elapsed = w.performance.now() - start;
            const metric = [...d.querySelectorAll('[data-testid=stMetric]')].find(e =>
                e.querySelector('[data-testid=stMetricLabel]')?.textContent.trim() === 'Direct-stock P/E');
            const value = metric?.querySelector('[data-testid=stMetricValue]')?.textContent.trim();
            if (visible(metric) && value && value !== w.fundamentalValues.at(-1)) {
                w.fundamentalValues.push(value);
                if (drawn === null) drawn = elapsed;
            }
            if (value === '20.00' && !running()) {
                const detail = [...d.querySelectorAll('[data-testid=stExpander]')].find(section =>
                    section.querySelector('summary')?.textContent.includes('Underlying company exposure'));
                if (!detail) {
                    if (elapsed > 60000) w.fundamentalVisit = {error:'Missing company detail'};
                    else w.requestAnimationFrame(frame);
                    return;
                }
                w.fundamentalVisit = {metric_ms:drawn, refreshed_ms:elapsed, values:w.fundamentalValues,
                    closed_company_grids:detail.querySelectorAll('[data-testid=stDataFrame]').length};
            } else if (elapsed > 60000 || exception()) w.fundamentalVisit = {error:'Fundamentals did not settle'};
            else w.requestAnimationFrame(frame);
        }
        w.requestAnimationFrame(frame); return true;
    """)
    if variant == 'candidate':
        try:
            wait(lambda: (trial / 'fundamental-waiting').exists())
            wait(lambda: read("return w.fundamentalValues?.includes('10.00');"))
            # A foreground rerender on each completed listing would publish a partial multiple.
            time.sleep(.7)
            if read('return w.fundamentalValues;') != ['10.00']:
                raise AssertionError('Analytics changed the cached multiple before the last listing completed')
        finally:
            (trial / 'release-fundamentals').touch()
    result = wait(lambda: read('return w.fundamentalVisit;'), timeout=65)
    result['event_range'] = [first_event, event_count(trial)]
    if 'error' in result:
        raise RuntimeError(result['error'])
    expected = ['10.00', '20.00'] if variant == 'candidate' else ['20.00']
    if result['values'] != expected:
        raise AssertionError(f"Unexpected displayed fundamentals: {result['values']}")
    if variant == 'candidate' and result['closed_company_grids']:
        raise AssertionError('Closed Analytics detail mounted company grids')
    return result


def exercise_exposure(evaluate, trial, variant, *, themes=False, company_accounts=False):
    """Time the first populated table frame and verify local source disclosure."""
    context = CONTEXT + """
        const tab = name => [...d.querySelectorAll('[role=tab]')].find(t => t.textContent.trim() === name);
        const table = () => {
            const host = d.querySelector('.st-key-tour_exposure_assets .stBidiComponent');
            if (!visible(host)) return null;
            const selector = 'table[aria-label="Exposure assets"]';
            return host.querySelector(selector) || [host,...host.querySelectorAll('*')]
                .map(e=>e.shadowRoot?.querySelector(selector)).find(Boolean);
        };
    """
    def read(js):
        return evaluate('(() => {' + context + js + '})()')

    def back_to_assets():
        read("""const assets = [...d.querySelectorAll('[role=radio], button')].find(e =>
            visible(e) && e.textContent.trim() === 'Assets');
            assets.scrollIntoView({block:'center'}); assets.click(); return true;""")
        wait(lambda: read("return !!table()?.querySelector('tr[data-position-id]') && !running();"))

    def company_visit(*, check_sources):
        read("""
            const button = [...d.querySelectorAll('[role=radio],button')].find(e =>
                visible(e) && e.textContent.trim() === 'Companies');
            button.scrollIntoView({block:'center'});
            w.companyVisit = null;
            const start = w.performance.now();
            button.click();
            let drawn = null;
            function frame() {
                const elapsed = w.performance.now() - start;
                const canvas = d.querySelector('.st-key-exposure_results [data-testid=stDataFrame] canvas');
                const grid = canvas?.querySelector('table[role=grid]');
                const rows = [...(grid?.querySelectorAll('tr') || [])].map(r =>
                    [...r.querySelectorAll('[role=gridcell]')].map(c => c.textContent)).filter(r => r.length);
                if (visible(canvas) && rows.length && drawn === null) drawn = elapsed;
                if (drawn !== null && rows.length >= 3 && !running()) {
                    w.companyVisit = {grid_ms:drawn, settled_ms:elapsed, companies:Number(grid.getAttribute('aria-rowcount'))-1,
                        sample:rows.slice(0,3)};
                } else if (elapsed > 60000 || exception()) w.companyVisit = {error:'Company holdings grid did not settle: ' +
                    JSON.stringify({selected:button.getAttribute('aria-checked'), running:running(), issuers:grid?.getAttribute('aria-rowcount'),
                        sampledRows:rows.length, exception:exception()})};
                else w.requestAnimationFrame(frame);
            }
            w.requestAnimationFrame(frame); return true;
        """)
        visit = wait(lambda: read('return w.companyVisit;'), timeout=65)
        if 'error' in visit:
            raise RuntimeError(visit['error'])
        if check_sources:
            panel = """
                const section = [...d.querySelectorAll('.st-key-exposure_results [data-testid=stExpander]')].find(e =>
                    e.querySelector('summary')?.textContent.includes('Company exposure by source'));
                const summary = section?.querySelector('summary');
            """
            read(panel + "summary.scrollIntoView({block:'center'}); summary.click(); return true;")
            visit['source_rows'] = wait(lambda: read(panel + """
                const grid = section?.querySelector('canvas table[role=grid]');
                const rows = Number(grid?.getAttribute('aria-rowcount')) - 1;
                return !running() && grid?.querySelector('[role=gridcell]') && rows > 0 && rows;
            """))
        else:
            back_to_assets()
        return visit

    def theme_visit():
        read("""
            const control = label => [...d.querySelectorAll('[role=radio], button')].find(e =>
                visible(e) && e.textContent.trim() === label);
            w.themeVisit = null;
            const start = w.performance.now();
            control('Themes & sectors').click();
            let drawn = null;
            function frame() {
                const plot = d.querySelector('.st-key-exposure_theme_results .js-plotly-plot');
                const trace = plot?._fullData?.[0], elapsed = w.performance.now() - start;
                const populated = visible(plot) && plot.querySelector('path.surface') &&
                    trace?.labels?.includes('Invented sector 0');
                if (populated && drawn === null) drawn = elapsed;
                if (populated && !running()) {
                    w.themeVisit = {chart_ms:drawn, settled_ms:elapsed,
                        sample:[Array.from(trace.labels), Array.from(trace.values)]};
                } else if (elapsed > 60000 || exception()) w.themeVisit = {error:'Sector chart did not settle'};
                else w.requestAnimationFrame(frame);
            }
            w.requestAnimationFrame(frame); return true;
        """)
        result = wait(lambda: read('return w.themeVisit;'), timeout=65)
        if 'error' in result:
            raise RuntimeError(result['error'])
        back_to_assets()
        return result

    def detail_visit():
        first_event = event_count(trial)
        read("""
            const row = table().querySelector('tbody tr[data-position-id]');
            const toggle = row.querySelector('[data-preview-toggle]');
            if (toggle.getAttribute('aria-expanded') !== 'true') toggle.click();
            w.detailVisit = null;
            const start = w.performance.now();
            row.querySelector('.source-details').click();
            let drawn = null;
            function frame() {
                const elapsed = w.performance.now() - start;
                const dialog = d.querySelector('[role="dialog"]');
                const hosts = dialog ? [...dialog.querySelectorAll('.stBidiComponent')] : [];
                const selector = 'table[aria-label="Exposure sources"]';
                const sources = hosts.map(h => h.querySelector(selector) || [h,...h.querySelectorAll('*')]
                    .map(e => e.shadowRoot?.querySelector(selector)).find(Boolean)).find(Boolean);
                const populated = visible(sources) && sources.tBodies[0]?.rows.length;
                if (populated && drawn === null) drawn = elapsed;
                if (populated && !running()) {
                    w.detailVisit = {sources_ms:drawn, sample:sources.tBodies[0].innerText};
                } else if (elapsed > 60000 || exception()) w.detailVisit = {error:'Asset sources did not settle'};
                else w.requestAnimationFrame(frame);
            }
            w.requestAnimationFrame(frame); return true;
        """)
        visit = wait(lambda: read('return w.detailVisit;'), timeout=65)
        if 'error' in visit:
            raise RuntimeError(visit['error'])
        read("""d.querySelector('[role="dialog"] button[aria-label="Close"]').click(); return true;""")
        wait(lambda: read("""return !visible(d.querySelector('[role="dialog"]')) && !!table() && !running();"""))
        time.sleep(.35)
        wait(lambda: read('return !!table() && !running() && !exception();'))
        visit['event_range'] = [first_event, event_count(trial)]
        return visit

    def ready():
        try:
            return read('return visible(allocation()) && !running() && !!tab("Exposure");')
        except Exception:
            return False  # Native splash documents are replaced during startup.

    wait(ready, timeout=100)
    ready_at = time.time()
    visits, theme_visits, details, companies = [], [], [], []
    visits_to_measure = 1 if company_accounts else 3
    for _ in range(visits_to_measure):
        read("""
            w.exposureVisit = null;
            const start = w.performance.now();
            tab('Exposure').click();
            let drawn = null;
            function frame() {
                const t = table(), elapsed = w.performance.now() - start;
                const rows = t?.querySelectorAll('tbody tr[data-position-id]');
                if (rows?.length && drawn === null) drawn = elapsed;
                if (rows?.length && tab('Exposure')?.getAttribute('aria-selected') === 'true' && !running()) {
                    const row = rows[0], toggle = row.querySelector('[data-preview-toggle]');
                    if (toggle.getAttribute('aria-expanded') === 'true') toggle.click();
                    toggle.click();
                    const preview = row.nextElementSibling?.querySelector('.preview-panel');
                    w.exposureVisit = {table_ms:drawn, settled_ms:elapsed, rows:rows.length,
                        sample: [row.innerText, preview?.innerText || '']};
                    toggle.click();
                } else if (elapsed > 60000 || exception()) w.exposureVisit = {error:'Exposure table did not settle'};
                else w.requestAnimationFrame(frame);
            }
            w.requestAnimationFrame(frame); return true;
        """)
        visit = wait(lambda: read('return w.exposureVisit;'), timeout=65)
        if 'error' in visit or not visit['sample'][1]:
            raise RuntimeError(visit)
        visits.append(visit)
        if themes and len(visits) == visits_to_measure:
            theme_visits = [theme_visit() for _ in range(2)]
            details = [detail_visit() for _ in range(2)]
        if (themes or company_accounts) and len(visits) == visits_to_measure:
            companies = [company_visit(check_sources=False), company_visit(check_sources=True)]
        read("tab('Overview').click(); return true;")
        wait(lambda: ready() and read("return !table() && tab('Overview').getAttribute('aria-selected') === 'true';"))
    fundamentals = exercise_fundamentals(evaluate, trial, variant) if themes else None
    return dict(ready_at=ready_at, visits=visits, themes=theme_visits, details=details, companies=companies, fundamentals=fundamentals)


def exercise_risk(evaluate, trial, *, await_refresh=False):
    context = CONTEXT + """
        const button = name => [...d.querySelectorAll('button,[role=radio]')].find(e => visible(e) &&
            (e.querySelector('p')?.textContent.trim() === name || e.textContent.trim() === name));
        const matrix = () => d.querySelector('.st-key-risk_correlations .js-plotly-plot');
    """
    def read(js):
        return evaluate('(() => {' + context + js + '})()')
    def ready():
        try:
            return read("return visible(allocation()) && !!button('Analytics') && !running();")
        except Exception:
            return False  # The native startup document is replaced.
    wait(ready, timeout=100)
    ready_at = time.time()
    wait(lambda: read("""const analytics = button('Analytics');
        if (!analytics || running()) return false;
        analytics.click(); return true;"""))
    actions = []
    steps = ['calculate', 'expand', 'collapse']
    if await_refresh:
        steps.insert(1, 'complete')
    for action in steps:
        start_event = actions[-1]['event_range'][1] if action == 'complete' else None
        def try_dispatch(js):
            nonlocal start_event
            if action != 'complete':
                start_event = event_count(trial)
            return read(js)
        wait(lambda: try_dispatch("""
            const action = """ + json.dumps(action) + """;
            if (exception()) throw Error('Risk view failed before ' + action);
            const current = matrix();
            const control = action === 'calculate' ? button('Calculate risk') :
                d.querySelector('.st-key-risk_correlations [role=switch]');
            if (running() || (action !== 'calculate' && !(visible(current) && current.data?.[0]?.z)) ||
                (action !== 'complete' && (!visible(control) || control.disabled))) return false;
            const previous = current?.layout?.height;
            const previousMatrix = action === 'complete' ? JSON.stringify(current.data[0].z) : null;
            w.riskVisit = null;
            const start = w.performance.now();
            if (action !== 'complete') {
                control.scrollIntoView({block:'center'});
                control.click();
            }
            let drawn = null;
            function frame() {
                const chart = matrix(), elapsed = w.performance.now() - start;
                const metrics = [...d.querySelectorAll('.st-key-tour_risk_metrics [data-testid=stMetricValue]')]
                    .map(e => e.textContent.trim());
                const changed = action === 'complete' ? chart?.data?.[0]?.z &&
                    JSON.stringify(chart.data[0].z) !== previousMatrix : chart?.layout?.height !== previous;
                const painted = visible(chart) && changed &&
                    !!chart.querySelector('.heatmaplayer image') && metrics.length === 3;
                if (painted && drawn === null) drawn = elapsed;
                if (painted && !running()) {
                    const trace = chart.data[0];
                    w.riskVisit = {chart_ms:drawn, settled_ms:elapsed, metrics, holdings:trace.x.length,
                        matrix:JSON.stringify(trace.z)};
                } else if (elapsed > 60000 || exception()) w.riskVisit = {error:'Risk matrix did not settle: ' + action};
                else w.requestAnimationFrame(frame);
            }
            w.requestAnimationFrame(frame); return true;
        """))
        if action == 'complete':
            (trial / 'release-risk').touch()
        result = wait(lambda: read('return w.riskVisit;'), timeout=65)
        if 'error' in result:
            raise RuntimeError(result['error'])
        result['digest'] = hashlib.sha256(result.pop('matrix').encode()).hexdigest()
        result['event_range'] = [start_event, event_count(trial)]
        actions.append(result)
    return dict(ready_at=ready_at, actions=actions)


def resident_bytes(pid):
    if sys.platform == 'win32':
        return int(subprocess.check_output(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command',
            f'(Get-Process -Id {int(pid)}).WorkingSet64'], text=True))
    return int(subprocess.check_output(['ps', '-o', 'rss=', '-p', str(pid)], text=True)) * 1024


def exercise_collection(evaluate, trial):
    def read(js):
        return evaluate('(() => {' + CONTEXT + """
            const named = (selector, name) => [...d.querySelectorAll(selector)].find(e =>
                visible(e) && e.textContent.trim() === name);
            const active = name => named('[role=tab]', name)?.getAttribute('aria-selected') === 'true';
            const companyGrid = () => d.querySelector('.st-key-exposure_results canvas table[role=grid]');
        """ + js + '})()')

    def ready():
        try:
            return read("return active('Overview') && visible(allocation()) && !running();")
        except Exception:
            return False  # Native startup document is replaced as the application opens.

    def switch(view):
        action = interact(evaluate, view, trial)
        # A finished web frame can precede server cleanup. Match the run that
        # actually enqueued this click's chart before taking a memory sample.
        def complete():
            lines = (trial / 'server.jsonl').read_bytes().split(b'\n')[action['event_range'][0]:-1]
            for index, line in enumerate(lines, start=action['event_range'][0]):
                event = json.loads(line)
                if event['event'] == 'script_finished' and event['chart_ms'] >= action['dispatch_ms']:
                    return index + 1
            return None
        action['event_range'][1] = wait(complete)
        return dict(view=view, **action)

    wait(ready, timeout=100)
    ready_at = time.time()
    pid = int((trial / 'server-pid').read_text())
    memory, actions = [], []
    series_digest = None
    for _batch in range(5):
        read("named('[role=tab]', 'Exposure').click(); return true;")
        wait(lambda: read("return active('Exposure') && !!named('[role=radio],button', 'Companies') && !running();"))
        read("""const button = named('[role=radio],button', 'Companies');
            if (button.getAttribute('aria-checked') !== 'true' && !companyGrid()) button.click();
            return true;""")
        wait(lambda: read("""
            const grid = companyGrid();
            return visible(grid?.closest('canvas')) && grid.getAttribute('aria-rowcount') === '8951' && !!grid.querySelector('[role=gridcell]') && !running();
        """))
        read("named('[role=tab]', 'Overview').click(); return true;")
        wait(ready)
        for _ in range(8):
            performance = switch('Performance')
            series = read("return JSON.stringify(history().data.map(t => [t.name,t.x,Array.from(t.y),t.customdata]));")
            digest = hashlib.sha256(series.encode()).hexdigest()
            if series_digest is not None and series_digest != digest:
                raise AssertionError('Performance series changed across switches')
            series_digest = digest
            actions.extend([performance, switch('Allocation')])
        memory.append(resident_bytes(pid))
    return dict(ready_at=ready_at, actions=actions, server_resident_bytes=memory, digest=series_digest)


def measure_view(evaluate, trial, variant, scenario, *, themes=False, company_accounts=False):
    if scenario == 'collection':
        return exercise_collection(evaluate, trial)
    if scenario in {'risk', 'risk-stale'}:
        return exercise_risk(evaluate, trial, await_refresh=scenario == 'risk-stale' and variant == 'candidate')
    if scenario == 'exposure':
        return exercise_exposure(evaluate, trial, variant, themes=themes, company_accounts=company_accounts)
    return exercise(evaluate, trial, scenario, persistent=variant != 'baseline')
