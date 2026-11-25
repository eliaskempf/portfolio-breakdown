"""Fixed synthetic provider and timing probes. Never bundled by release builds."""
from dataclasses import replace
from functools import wraps
import json
import os
import time
from threading import Lock


def observe_server(trial):
    from portfolio_app import application

    record_lock = Lock()

    def record(event, **fields):
        with record_lock, (trial / 'server.jsonl').open('a', encoding='utf-8') as out:
            print(json.dumps({'event':event, 'monotonic_ms':time.monotonic_ns() / 1e6, **fields}), file=out)

    def counted(function, event):
        @wraps(function)
        def call(*args, **kwargs):
            record(event)
            return function(*args, **kwargs)
        return call

    application.prepare_application = counted(application.prepare_application, 'portfolio_valued')
    scenario = os.environ['PORTFOLIO_BENCHMARK_REFRESH']
    if scenario in {'cash-history', 'long-history'}:
        from threading import local
        from portfolio_app.performance import history_view as views, personal_performance as performance
        from portfolio_app.performance.personal_history_refresh import refresher

        refresher.download = lambda directory, journal, scope, store, start, end, target: (store, ())
        work = local()
        original_capital = performance._period_capital
        def measured_capital(calculation, scope, *args):
            if getattr(work, 'positions', None) is not None and len(scope.positions) == 1 and not scope.accounts:
                work.positions += 1
            return original_capital(calculation, scope, *args)

        if scenario == 'long-history':
            from portfolio_app.performance import gain_accounting
            original_members = gain_accounting.affected_positions
            def measured_members(book, event):
                if event.estimated and getattr(work, 'scope', None) is not None:
                    work.estimate_checks += 1
                return original_members(book, event)
            gain_accounting.affected_positions = measured_members

            original_fx = performance.valuation_fx_quotes
            def measured_fx(*args, **kwargs):
                if getattr(work, 'scope', None) is not None:
                    work.fx_lookups += 1
                return original_fx(*args, **kwargs)
            performance.valuation_fx_quotes = measured_fx

            original_value = performance._resolve_close
            def measured_value(journal, scope, *args, **kwargs):
                selected = getattr(work, 'scope', None)
                if selected is not None:
                    work.valuations += 1
                if scope == selected:
                    work.closes += 1
                elif selected is not None and len(scope.positions) + len(scope.accounts) == 1:
                    work.member_closes += 1
                return original_value(journal, scope, *args, **kwargs)
            performance._resolve_close = measured_value

        original_chart = views.history_view
        def measured_chart(*args, **kwargs):
            work.positions = work.closes = work.member_closes = work.valuations = work.fx_lookups = work.estimate_checks = 0
            work.scope = args[1] if len(args) > 1 else kwargs['scope']
            try:
                result = original_chart(*args, **kwargs)
                record('cash_history_calculated', points=len(result.points), positions=work.positions,
                       closes=work.closes, member_closes=work.member_closes,
                       valuations=work.valuations, fx_lookups=work.fx_lookups,
                       estimate_checks=work.estimate_checks, estimated_gain=result.all_known.coverage_note == 'Estimated gain')
                return result
            finally:
                del work.positions, work.closes, work.member_closes, work.valuations, work.fx_lookups, work.estimate_checks, work.scope

        performance._period_capital = measured_capital
        views.history_view = measured_chart
        return
    if scenario == 'purchase-fx':
        from datetime import date, timedelta
        import pandas as pd
        import yfinance
        from portfolio_app.market.fx import YahooFXHistory
        from portfolio_app.performance.personal_history_refresh import refresher
        from perf_benchmark.renderer import wait

        def fetch(self, currency, start, end):
            day = date.fromisoformat(end) - timedelta(days=1)
            record('purchase_fx_requested', currency=currency, day=day.isoformat())
            (trial / 'fx-pending').touch()
            wait(lambda: (trial / 'release-fx').exists(), timeout=120)
            time.sleep(.075)  # Each independent rate arrives asynchronously, including later requests.
            return pd.Series(.9, index=pd.date_range(start, day))

        def unexpected(ticker, *args, **kwargs):
            record('unexpected_listing', ticker=ticker)
            raise AssertionError(f'Unexpected live listing in purchase FX benchmark: {ticker}')

        prepare = application.prepare_application
        def observe_costs(*args, **kwargs):
            result = prepare(*args, **kwargs)
            costs = result.valued.cost_basis_reporting
            complete = bool(costs.notna().all())
            record('purchase_costs', covered=int(costs.count()), total=float(costs.sum()),
                   distinct=sorted(set(map(float, costs.dropna()))))
            if complete:
                (trial / 'fx-published').touch()
            return result

        YahooFXHistory.fetch = fetch
        yfinance.Ticker = unexpected
        refresher.download = lambda directory, journal, scope, store, start, end, target: (store, ())
        application.prepare_application = observe_costs
        return
    if scenario == 'market-chart':
        from ipaddress import ip_address
        from pathlib import Path
        import socket
        import sys
        from portfolio_app.market import history
        from portfolio_app.market.msci_history import MarketHistoryProvider
        directory = (trial.parent / 'synthetic' / '.cache' / 'history').resolve()
        names = {path.name for path in directory.glob('*.json')}
        def observe(event, args):
            if event == 'open' and isinstance(args[0], str) and args[0].endswith('.json'):
                path = Path(args[0])
                if path.name in names and path.parent.resolve() == directory:
                    record('history_file_opened')
            if event == 'socket.connect' and args[0].family in (socket.AF_INET, socket.AF_INET6):
                host = args[1][0]
                if host == 'localhost':
                    return
                try:
                    local = ip_address(host).is_loopback
                except ValueError:
                    local = False
                if not local:
                    record('unexpected_market_network')
                    raise OSError('Offline market chart benchmark')
        sys.addaudithook(observe)
        def unexpected_fetch(self, ticker, period):
            record('unexpected_market_fetch')
            raise AssertionError('The benchmark should use saved prices and FX')
        MarketHistoryProvider.history = unexpected_fetch
        from streamlit.runtime.app_session import AppSession
        from streamlit.runtime.scriptrunner_utils.script_run_context import ScriptRunContext

        handle_request = AppSession._handle_rerun_script_request
        def observe_market_request(session, client_state=None):
            if client_state is not None:
                fragment = client_state.fragment_id
                period = next((w for w in client_state.widget_states.widgets
                               if 'position_edit_history_' in w.id and w.id.endswith('_period')), None)
                record('market_range_requested', selection=list(period.string_array_value.data) if period else None,
                       fragment=fragment, registered=not fragment or session._fragment_storage.contains(fragment),
                       automatic=client_state.is_auto_rerun)
            return handle_request(session, client_state)

        enqueue = ScriptRunContext.enqueue
        def observe_market_chart(ctx, msg):
            result = enqueue(ctx, msg)
            if msg.HasField('delta') and msg.delta.new_element.HasField('plotly_chart'):
                record('market_chart_enqueued', path=list(msg.metadata.delta_path), fragment=msg.delta.fragment_id,
                       bytes=len(msg.delta.new_element.plotly_chart.spec), reference=msg.hash in ctx.cached_message_hashes)
            return result

        AppSession._handle_rerun_script_request = observe_market_request
        ScriptRunContext.enqueue = observe_market_chart
        convert = history.convert_price_history
        def measured_conversion(*args, **kwargs):
            start = time.perf_counter_ns()
            result = convert(*args, **kwargs)
            record('converted', points=len(result.prices), elapsed_ms=(time.perf_counter_ns() - start) / 1e6)
            return result
        history.convert_price_history = measured_conversion
        return
    if scenario == 'checklist':
        from portfolio_app.interface.views.history import history_status_ui
        history_status_ui.position_history_statuses = counted(history_status_ui.position_history_statuses, 'checklist_calculated')
        return
    if scenario == 'collection':
        import yfinance
        from portfolio_app.performance.personal_history_refresh import refresher
        def unexpected_listing(ticker, *args, **kwargs):
            record('unexpected_listing', ticker=ticker)
            raise AssertionError(f'Collection benchmark requested a live listing: {ticker}')
        yfinance.Ticker = unexpected_listing
        refresher.download = lambda directory, journal, scope, store, start, end, target: (store, ())
        observe_collections(trial, record)
        return
    if scenario.startswith('risk'):
        from portfolio_app.analytics import analytics_service
        from portfolio_app.analytics.risk_data import DemoRiskHistoryProvider, MarketRiskHistoryProvider
        def adjusted(self, ticker, years):
            started = time.monotonic_ns() / 1e6
            time.sleep(.03)
            record('adjusted_fetched', ticker=ticker, started_ms=started)
            history = DemoRiskHistoryProvider.fetch(self, ticker, years)
            if scenario == 'risk-stale' and ticker == 'ZZC-0':
                if os.environ['PORTFOLIO_BENCHMARK_VARIANT'] == 'candidate':
                    from perf_benchmark.renderer import wait
                    wait(lambda: (trial / 'release-risk').exists())
                friday = history.prices.index[history.prices.index.dayofweek == 4][-1]
                history.prices.loc[friday] *= 1.35
            return history
        MarketRiskHistoryProvider.fetch = adjusted
        analytics_service.risk_analytics = counted(analytics_service.risk_analytics, 'risk_calculated')
        return
    if scenario == 'exposure':
        if os.environ.get('PORTFOLIO_BENCHMARK_COMPANY_ACCOUNTS') == '1':
            from portfolio_app.ledger import account_journal
            account_journal.load_journal = counted(account_journal.load_journal, 'journal_read')
        if os.environ.get('PORTFOLIO_BENCHMARK_THEMES') == '1':
            from perf_benchmark.fixture import benchmark_fundamentals
            from portfolio_app.analytics.fundamentals import YahooFundamentalsProvider
            def fetch(self, ticker):
                if ticker == 'ZZF-23' and os.environ['PORTFOLIO_BENCHMARK_VARIANT'] == 'candidate':
                    (trial / 'fundamental-waiting').touch()
                    deadline = time.monotonic() + 60
                    while not (trial / 'release-fundamentals').exists():
                        if time.monotonic() > deadline:
                            raise TimeoutError('Synthetic fundamentals release was not received')
                        time.sleep(.05)
                time.sleep(.12)
                record('fundamental_fetched', ticker=ticker)
                return benchmark_fundamentals(ticker, 20.)
            YahooFundamentalsProvider.fetch = fetch
        return
    from portfolio_app.performance.personal_history_refresh import refresher
    from portfolio_app.performance import history_view as views
    from streamlit.runtime.scriptrunner_utils.script_run_context import ScriptRunContext

    if scenario == 'quote-pending':
        from datetime import datetime, timezone
        from perf_benchmark.renderer import wait
        from portfolio_app.market.market_data import services
        from portfolio_app.market.prices import Quote
        settings = json.loads((trial.parent / 'quote-check.json').read_bytes())
        requests = {r['key']: (index, r) for index, r in enumerate(settings['requests'])}
        class Provider:
            def get(self, key):
                index, request = requests[key]
                (trial / f'quote-pending-{index}').touch()
                wait(lambda: (trial / f'release-quote-{index}').exists(), timeout=180)
                return Quote(request['corrected'], request['currency'], datetime.now(timezone.utc))

            def price(self, ticker):
                return self.get('price:' + ticker)

            def fx(self, currency):
                return self.get('fx:' + currency)
        service = services(trial.parent / 'synthetic')[0]
        service.provider = Provider()
        def observe_quote(kind, fetch):
            def observed(value, **options):
                result = fetch(value, **options)
                if result.status == 'fresh':
                    index, _ = requests[f'{kind}:{value}']
                    (trial / f'quote-fetched-{index}').touch()
                return result
            return observed
        service.price = observe_quote('price', service.price)
        service.fx = observe_quote('fx', service.fx)
    if scenario.startswith('etf-'):
        from portfolio_app.funds.etf_refresh import coordinator as funds, SOURCES
        from portfolio_app.funds.etf_sources import publish_snapshot
        from portfolio_app.funds.etf import validate_constituents
        import pandas as pd
        # This registry entry exists only in the disposable benchmark process.
        SOURCES['ZZF000000000'] = None
        def refresh(fund):
            from perf_benchmark.renderer import wait
            (trial / 'etf-pending').touch()
            wait(lambda: (trial / 'release-etf').exists(), timeout=100)
            if scenario.endswith('-failed'):
                raise ValueError('Invented ETF provider unavailable')
            frame = pd.read_csv(fund.manifest_path.parent / 'fund-0.csv', dtype=str, keep_default_na=False)
            return publish_snapshot(fund, validate_constituents(frame), fund.as_of, prior=fund.manifest_path.read_bytes())
        funds.refresh = refresh

    def download(directory, journal, scope, store, start, end, target):
        from perf_benchmark.renderer import wait
        (trial / 'pending').touch()
        wait(lambda: (trial / 'release').exists(), timeout=100)
        if os.environ['PORTFOLIO_BENCHMARK_REFRESH'] == 'changed':
            last = next(o for o in store.observations if o.id == 'o-0-730')
            corrected = replace(last, id='benchmark-correction', replaces=last.id, amount=last.amount + 1)
            store = replace(store, observations=(*store.observations, corrected))
        (trial / 'completed').touch()
        return store, ()

    refresher.download = download
    original_view = views.history_view
    enqueue = ScriptRunContext.enqueue
    def calculate(*args, **kwargs):
        from threading import current_thread
        if current_thread().name.startswith('market-data') and not (trial / 'recalculating').exists():
            from perf_benchmark.renderer import wait
            (trial / 'recalculating').touch()
            wait(lambda: (trial / 'finish-calc').exists(), timeout=30)
        start = time.perf_counter()
        result = original_view(*args, **kwargs)
        record('history_calculated', duration_ms=(time.perf_counter()-start)*1000)
        return result
    def observe_enqueue(ctx, msg):
        if msg.HasField('delta') and msg.delta.new_element.HasField('plotly_chart'):
            record('chart_enqueued', bytes=msg.ByteSize())
        return enqueue(ctx, msg)
    from portfolio_app.ledger import security_history
    security_history.history_fingerprint = counted(security_history.history_fingerprint, 'journal_fingerprinted')
    variant = os.environ['PORTFOLIO_BENCHMARK_VARIANT']
    if variant == 'navigation-reference':
        from portfolio_app.interface.shared import overview_navigation
        overview_navigation.render_panel = lambda draw, is_current: draw()
        from portfolio_app.interface.views.history import history_ui
        prepare = history_ui.prepared_history_calculation
        def without_revision(*args, **kwargs):
            kwargs.pop('journal_revision', None)
            return prepare(*args, **kwargs)
        history_ui.prepared_history_calculation = without_revision
        request = refresher.request
        def without_fingerprint(*args, **kwargs):
            kwargs.pop('fingerprint', None)
            return request(*args, **kwargs)
        refresher.request = without_fingerprint
    views.history_view = calculate
    ScriptRunContext.enqueue = observe_enqueue
    from portfolio_app.performance import history_update, history_view_cache
    if os.environ['PORTFOLIO_BENCHMARK_VARIANT'] == 'baseline':
        from concurrent.futures import Future
        # Use the existing synchronous fallback, with persistence bypassed as well.
        reference = Future()
        reference.set_exception(RuntimeError('Synchronous benchmark reference'))
        history_update.start_update = lambda *args: reference
        history_view_cache.load_or_compute = lambda directory, signature, compute: compute()
    else:
        start_update = history_update.start_update
        def contend_once(*args):
            if not (trial / 'contended').exists():
                (trial / 'contended').touch()
                return None
            return start_update(*args)
        history_update.start_update = contend_once
        original_load = history_view_cache.load_or_compute
        def observe_cache(directory, signature, compute):
            missed = False
            def miss():
                nonlocal missed
                missed = True
                return compute()
            result = original_load(directory, signature, miss)
            record('disk_miss' if missed else 'disk_hit', chart=signature[-2])
            return result
        history_view_cache.load_or_compute = observe_cache


def observe_collections(trial, record):
    import gc
    from threading import local
    from streamlit import config
    from portfolio_app.performance.history_observations import _observations_from_bytes
    from streamlit.runtime.scriptrunner.script_runner import ScriptRunner
    from streamlit.runtime.scriptrunner_utils.script_run_context import ScriptRunContext

    (trial / 'server-pid').write_text(str(os.getpid()), encoding='ascii')
    state = local()
    enqueue = ScriptRunContext.enqueue
    collect = gc.collect
    finish = ScriptRunner._on_script_finished

    def chart(ctx, msg):
        if msg.HasField('delta') and msg.delta.new_element.HasField('plotly_chart'):
            state.chart_ms = time.monotonic_ns() / 1e6
        return enqueue(ctx, msg)

    def count_collect(generation=2):
        if generation == 2 and getattr(state, 'in_cleanup', False):
            state.forced += 1
        return collect(generation)

    def finished(runner, *args, **kwargs):
        before = gc.get_stats()[2]['collections']
        state.forced = 0
        state.in_cleanup = True
        try:
            return finish(runner, *args, **kwargs)
        finally:
            state.in_cleanup = False
            record('script_finished', chart_ms=getattr(state, 'chart_ms', 0), forced=state.forced,
                   policy=config.get_option('runner.postScriptGC'), automatic=gc.isenabled(),
                   observation_parses=_observations_from_bytes.cache_info().misses,
                   cleanup_collections=gc.get_stats()[2]['collections'] - before)
            state.chart_ms = 0

    ScriptRunContext.enqueue = chart
    gc.collect = count_collect
    ScriptRunner._on_script_finished = finished


def observe_window(trial):
    import webview
    from perf_benchmark.renderer import measure_view
    create_window = webview.create_window
    def create(*args, **kwargs):
        window = create_window(*args, **kwargs)
        def inspect():
            try:
                scenario = os.environ['PORTFOLIO_BENCHMARK_REFRESH']
                metrics = measure_view(window.evaluate_js, trial, os.environ['PORTFOLIO_BENCHMARK_VARIANT'], scenario,
                    themes=os.environ.get('PORTFOLIO_BENCHMARK_THEMES') == '1',
                    company_accounts=os.environ.get('PORTFOLIO_BENCHMARK_COMPANY_ACCOUNTS') == '1')
            except Exception:
                import traceback
                metrics = {'error':traceback.format_exc()}
            (trial / 'window.json').write_text(json.dumps(metrics), encoding='utf-8')
            window.destroy()
        from threading import Thread
        worker = Thread(target=inspect, daemon=True)
        def shown():
            if worker.ident is None:
                worker.start()
        window.events.shown += shown
        return window
    webview.create_window = create
