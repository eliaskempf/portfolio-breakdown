"""PyInstaller hook for disposable benchmark binaries only."""
import os
import sys
from pathlib import Path

if os.environ.get('PORTFOLIO_BENCHMARK_TRIAL'):
    from perf_benchmark import instrumentation
    trial = Path(os.environ['PORTFOLIO_BENCHMARK_TRIAL'])
    if sys.argv[1:2] in (['--internal-window-server'], ['--internal-streamlit']):
        instrumentation.observe_server(trial)
    elif '--data-dir' in sys.argv and '--browser' not in sys.argv and '--stop' not in sys.argv:
        instrumentation.observe_window(trial)
