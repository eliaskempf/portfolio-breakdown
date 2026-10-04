"""Pipe-owned POSIX supervisor. Process IDs are never loaded from disk."""
from contextlib import contextmanager
import os
import signal
import select
import subprocess
import sys
import threading
import time


def internal_command(command: list[str]) -> list[str]:
    from portfolio_app.window_process import gated_command
    result = gated_command(command)
    result[result.index('--internal-window-server')] = '--internal-posix-supervisor'
    return result


def supervise(command: list[str], stream) -> int:
    """Retain the child's PID until its whole process group has been stopped.

    This small independent process survives a window crash; EOF on its inherited
    pipe requests cleanup. WNOWAIT prevents PID reuse before group termination.
    """
    if stream is None or stream.readline() != b'start\n':
        return 0
    stopping = threading.Event()
    previous = signal.signal(signal.SIGTERM, lambda *_: stopping.set())
    watch_done = threading.Event()
    def watch_parent():
        # Do not hold BufferedReader's lock during interpreter shutdown when
        # the application exits before its window closes the pipe.
        try:
            while not watch_done.is_set():
                readable, _, _ = select.select([stream.fileno()], [], [], .2)
                if readable and not os.read(stream.fileno(), 4096):
                    stopping.set()
                    return
        except OSError:
            stopping.set()
    watcher = threading.Thread(target=watch_parent, daemon=True)
    watcher.start()
    child = None
    try:
        child = subprocess.Popen(command, stdin=subprocess.DEVNULL, start_new_session=True, close_fds=True)
        def exited():
            return os.waitid(os.P_PID, child.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT) is not None
        while not stopping.wait(.1) and not exited():
            pass
    finally:
        if child is not None:
            # Do not poll/wait (reap) before killpg: the unreaped group leader
            # reserves the PID even if the application exits before descendants.
            def signal_group(sig):
                try:
                    os.killpg(child.pid, sig)
                except ProcessLookupError:
                    pass  # An empty group has no live descendants to stop.
            signal_group(signal.SIGTERM)
            deadline = time.monotonic() + 10
            while not exited() and time.monotonic() < deadline:
                time.sleep(.1)
            signal_group(signal.SIGKILL)
            code = child.wait(timeout=5)
        watch_done.set()
        watcher.join(timeout=1)
        signal.signal(signal.SIGTERM, previous)
    return 0 if stopping.is_set() else code


def supervisor_main():
    from portfolio_app.window import control_input
    command = ([sys.executable, '--internal-streamlit', *sys.argv[2:]]
               if getattr(sys, 'frozen', False) else
               [sys.executable, '-m', 'streamlit', *sys.argv[2:]])
    raise SystemExit(supervise(command, control_input()))


@contextmanager
def contained_child(command: list[str]):
    child = subprocess.Popen(internal_command(command), stdin=subprocess.PIPE,
                             start_new_session=True, close_fds=True)
    try:
        child.stdin.write(b'start\n')
        child.stdin.flush()
        yield child
    finally:
        try:
            child.stdin.close()
        except OSError:
            pass
        try:
            child.wait(timeout=20)
        except subprocess.TimeoutExpired:
            child.terminate()
            child.wait(timeout=15)
