"""Console-free desktop entry, also runnable from source for launch iteration."""
from contextlib import ExitStack
import sys
import traceback


def main():
    from portfolio_app.settings import state_path
    original = sys.stdout, sys.stderr
    with ExitStack() as stack:
        # pythonw and PyInstaller's windowed bootloader provide no standard
        # output streams. Give imports and errors a real private log destination.
        if sys.stdout is None or sys.stderr is None:
            log = state_path() / 'launcher.log'
            log.parent.mkdir(parents=True, exist_ok=True)
            output = stack.enter_context(log.open('a', encoding='utf-8'))
            if sys.stdout is None:
                sys.stdout = output
            if sys.stderr is None:
                sys.stderr = output
        try:
            from portfolio_app.app import main as app_main
            if '--foreground' not in sys.argv and '--desktop' not in sys.argv:
                sys.argv.append('--desktop')
            app_main()
        except Exception as exc:
            traceback.print_exc()
            from portfolio_app.desktop import startup_error
            startup_error(f'Application could not start: {exc}')
            raise SystemExit(1) from None
        finally:
            sys.stdout, sys.stderr = original


if __name__ == '__main__':
    main()
