"""Serve only bundled public guides, independent of portfolio directories."""
from contextlib import contextmanager
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import os
from pathlib import Path
import sys
import threading


@contextmanager
def documentation_server():
    root = (Path(sys.executable).parent / 'documentation' if getattr(sys, 'frozen', False)
            else Path(__file__).resolve().parents[2] / 'dist/docs-site')
    if getattr(sys, 'frozen', False) and sys.platform == 'darwin':
        root = Path(sys.executable).parent.parent / 'Resources/documentation'
    if not (root / 'build-info.json').is_file():
        yield
        return
    class Handler(SimpleHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def list_directory(self, path):
            self.send_error(404)

    server = ThreadingHTTPServer(('127.0.0.1', 0), partial(Handler, directory=str(root)))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    previous = os.environ.get('PORTFOLIO_DOCS_URL')
    os.environ['PORTFOLIO_DOCS_URL'] = f'http://127.0.0.1:{server.server_port}/'
    thread.start()
    try:
        yield
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
        if previous is None:
            os.environ.pop('PORTFOLIO_DOCS_URL', None)
        else:
            os.environ['PORTFOLIO_DOCS_URL'] = previous


def guide_url(topic=''):
    """Packages link to their exact offline copy; source previews use dev guides."""
    return os.environ.get('PORTFOLIO_DOCS_URL', 'https://eliaskempf.github.io/portfolio-breakdown/dev/') + topic
