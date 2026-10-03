"""PyInstaller entrypoint; the application owns all launch behavior."""
from pathlib import Path
import sys

if sys.platform == 'win32' and Path(sys.executable).stem == 'Portfolio Breakdown':
    from portfolio_app.gui import main
else:
    from portfolio_app.app import main

if __name__ == '__main__':
    main()
