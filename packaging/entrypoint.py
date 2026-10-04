"""PyInstaller entrypoint; the application owns all launch behavior."""
from pathlib import Path
import sys

if sys.platform == 'win32' and sys.argv[1:2] == ['--internal-window-server']:
    from portfolio_app.window import main
elif sys.platform == 'win32' and Path(sys.executable).stem == 'Portfolio Breakdown':
    from portfolio_app.gui import main
else:
    from portfolio_app.app import main
    # The console companion retains headless/CLI diagnostics and browser mode.
    if '--browser' in sys.argv:
        sys.argv.remove('--browser')

if __name__ == '__main__':
    main()
