"""Fixed synthetic provider and timing probes. Never bundled by release builds."""
from dataclasses import replace
import json
import os
import time


def observe_server(trial):
    from portfolio_app.performance.personal_history_refresh import refresher
    from portfolio_app.performance import history_view as views
    from streamlit.runtime.scriptrunner_utils.script_run_context import ScriptRunContext

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
    def record(event, **fields):
        with (trial / 'server.jsonl').open('a', encoding='utf-8') as out:
            print(json.dumps({'event':event, 'epoch_ms':time.time_ns() / 1e6, **fields}), file=out)
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
    from portfolio_app import application
    from portfolio_app.ledger import security_history
    from functools import wraps

    def counted(function, event):
        @wraps(function)
        def call(*args, **kwargs):
            record(event)
            return function(*args, **kwargs)
        return call

    application.prepare_application = counted(application.prepare_application, 'portfolio_valued')
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


def observe_window(trial):
    import webview
    from perf_benchmark.renderer import exercise
    create_window = webview.create_window
    def create(*args, **kwargs):
        window = create_window(*args, **kwargs)
        def inspect():
            try:
                metrics = exercise(window.evaluate_js, trial, os.environ['PORTFOLIO_BENCHMARK_REFRESH'],
                                   persistent=os.environ['PORTFOLIO_BENCHMARK_VARIANT'] != 'baseline')
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
