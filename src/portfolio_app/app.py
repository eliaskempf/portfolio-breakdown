"""Installed command-line entrypoint for the local Streamlit application."""

import argparse
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory

from portfolio_app.demo import create_demo_data


def main() -> None:
    parser = argparse.ArgumentParser(description="Local portfolio allocation explorer")
    parser.add_argument("--data-dir", type=Path, help="Persistent private portfolio directory (default: ./data/portfolio)")
    parser.add_argument("--demo", action="store_true", help="Open the editable offline demo; dummy data resets on each app start")
    args, streamlit_args = parser.parse_known_args()
    data_dir = args.data_dir or Path.cwd() / "data" / "portfolio"
    # One writable demo per server process. Reruns/tabs share it; the next launch
    # creates a new one, even after an unclean shutdown. Never reset personal files.
    with TemporaryDirectory(prefix="portfolio-demo-") as temporary:
        demo_dir = create_demo_data(Path(temporary))
        # Native appearance settings retain the browser's light/dark choice and
        # apply it to widgets, data editors, and chart templates together.
        theme = ["--theme.primaryColor=#5470c6", "--theme.baseRadius=small",
                 "--theme.light.backgroundColor=#ffffff", "--theme.light.secondaryBackgroundColor=#f4f5f7",
                 "--theme.light.textColor=#202632", "--theme.dark.backgroundColor=#11151c",
                 "--theme.dark.secondaryBackgroundColor=#1a202b", "--theme.dark.textColor=#e3e7ef"]
        command = [sys.executable, "-m", "streamlit", "run", str(Path(__file__).with_name("ui.py")), *theme, *streamlit_args,
                   "--", "--data-dir", str(data_dir.resolve()), "--demo-dir", str(demo_dir)]
        if args.demo:
            command.append("--demo")
        try:
            raise SystemExit(subprocess.call(command))
        except KeyboardInterrupt:
            raise SystemExit(130) from None


if __name__ == "__main__":
    main()
