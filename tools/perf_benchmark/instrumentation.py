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
            corrected = replace(last, id='benchmark-correction', amount=last.amount + 1)
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
        start = time.perf_counter()
        result = original_view(*args, **kwargs)
        record('history_calculated', duration_ms=(time.perf_counter()-start)*1000)
        return result
    def observe_enqueue(ctx, msg):
        if msg.HasField('delta') and msg.delta.new_element.HasField('plotly_chart'):
            record('chart_enqueued', bytes=msg.ByteSize())
        return enqueue(ctx, msg)
    views.history_view = calculate
    ScriptRunContext.enqueue = observe_enqueue
    from portfolio_app.performance import history_view_cache
    if os.environ['PORTFOLIO_BENCHMARK_VARIANT'] == 'baseline':
        # The reference uses the same binary with only cross-session view reuse bypassed.
        history_view_cache.load_or_compute = lambda directory, signature, compute: compute()
    else:
        original_load = history_view_cache.load_or_compute
        def observe_cache(directory, signature, compute):
            missed = False
            def miss():
                nonlocal missed
                missed = True
                return compute()
            result = original_load(directory, signature, miss)
            record('disk_miss' if missed else 'disk_hit')
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
                metrics = exercise(window.evaluate_js, trial, os.environ['PORTFOLIO_BENCHMARK_REFRESH'])
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
