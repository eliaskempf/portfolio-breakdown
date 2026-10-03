"""Installed and frozen entrypoint; source development uses the same application."""
import argparse
from pathlib import Path
import sys
from tempfile import TemporaryDirectory

from portfolio_app.demo import create_demo_data
from portfolio_app.holdings import DataError
from portfolio_app.launcher import (choose_port, open_existing, run_server, start_desktop,
                                    stop_instance, workspace_lease)
from portfolio_app.settings import app_version, theme_options, workspace_path


def main() -> None:
    if sys.argv[1:2] == ['--internal-streamlit']:
        from streamlit.web.cli import main as streamlit_main
        sys.argv = ['streamlit', *sys.argv[2:]]
        streamlit_main()
        return
    parser = argparse.ArgumentParser(description='Local portfolio allocation explorer')
    parser.add_argument('--version', action='version', version=app_version())
    parser.add_argument('--data-dir', type=Path, help='Private workspace (default: platform user-data directory)')
    parser.add_argument('--demo', action='store_true', help='Editable demo with public market data, reset on each server start')
    parser.add_argument('--offline-demo', action='store_true', help='Use synthetic offline data in the demo workspace')
    parser.add_argument('--skip-intro', action='store_true', help='Skip the startup animation for this server')
    parser.add_argument('--desktop', action='store_true', help='Launch in background and open the browser')
    parser.add_argument('--foreground', action='store_true', help='Keep the server attached to this terminal')
    parser.add_argument('--no-browser', action='store_true', help='Do not open a browser automatically')
    parser.add_argument('--server.port', dest='port', type=int)
    parser.add_argument('--server.headless', dest='headless', choices=['true', 'false'], default='false')
    action = parser.add_mutually_exclusive_group()
    action.add_argument('--stop', action='store_true', help='Stop the managed server for this workspace')
    action.add_argument('--show-data-dir', action='store_true')
    action.add_argument('--open-data-dir', action='store_true')
    action.add_argument('--install-shortcut', action='store_true')
    action.add_argument('--migrate-from', type=Path, help='Copy an existing workspace into the NEW --data-dir')
    action.add_argument('--backup-to', type=Path, help='Copy the stopped workspace to a NEW backup directory')
    action.add_argument('--restore-from', type=Path, help='Restore a backup into the NEW --data-dir')
    args, streamlit_args = parser.parse_known_args()
    directory = workspace_path(args.data_dir)
    try:
        if args.show_data_dir:
            print(directory)
            return
        if args.stop:
            print('Stopped.' if stop_instance(directory) else 'No managed server is running for this workspace.')
            return
        if args.open_data_dir or args.install_shortcut:
            from portfolio_app.desktop import install_shortcut, open_folder
            if args.open_data_dir:
                open_folder(directory)
            else:
                print(install_shortcut(directory))
            return
        if args.migrate_from or args.restore_from or args.backup_to:
            from portfolio_app.workspace import copy_workspace
            source = args.migrate_from or args.restore_from or directory
            print(copy_workspace(source, args.backup_to or directory))
            return
        if args.port is not None and not 1 <= args.port <= 65535:
            parser.error('--server.port must be between 1 and 65535')
        browser = not args.no_browser and args.headless != 'true'
        if not args.foreground and (args.desktop or getattr(sys, 'frozen', False)):
            forwarded = ['--data-dir', str(directory), '--server.headless', args.headless, *streamlit_args]
            if args.demo:
                forwarded.append('--demo')
            if args.offline_demo:
                forwarded.append('--offline-demo')
            if args.skip_intro:
                forwarded.append('--skip-intro')
            if args.port:
                forwarded += ['--server.port', str(args.port)]
            start_desktop(forwarded, directory, demo=args.demo, browser=browser)
            return
        if open_existing(directory, demo=args.demo, browser=browser):
            return
        with workspace_lease(directory):
            port = choose_port(args.port)
            with TemporaryDirectory(prefix='portfolio-demo-') as temporary:
                demo_dir = create_demo_data(Path(temporary), live=not args.offline_demo)
                prefix = [sys.executable, '--internal-streamlit'] if getattr(sys, 'frozen', False) else [sys.executable, '-m', 'streamlit']
                command = [*prefix, 'run', str(Path(__file__).with_name('ui.py')), *theme_options(), *streamlit_args,
                           '--server.address=127.0.0.1', f'--server.port={port}', '--server.headless=true',
                           '--browser.gatherUsageStats=false']
                if getattr(sys, 'frozen', False):
                    command += ['--server.fileWatcherType=none', '--global.developmentMode=false']
                command += ['--', '--data-dir', str(directory), '--demo-dir', str(demo_dir)]
                if args.skip_intro:
                    command.append('--skip-intro')
                if args.demo:
                    command.append('--demo')
                raise SystemExit(run_server(command, directory, port=port, browser=browser, demo=args.demo))
    except KeyboardInterrupt:
        raise SystemExit(130) from None
    except (DataError, OSError) as exc:
        if args.desktop or (getattr(sys, 'frozen', False) and not args.foreground):
            from portfolio_app.desktop import startup_error
            startup_error(str(exc))
        parser.exit(1, f'Portfolio could not complete the operation: {exc}\n')


if __name__ == '__main__':
    main()
