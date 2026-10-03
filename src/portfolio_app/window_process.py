"""Experimental Windows child containment; never accepts a recorded process ID."""
from contextlib import contextmanager
import ctypes
from ctypes import wintypes
import os
import subprocess
import sys
import time

from portfolio_app.holdings import DataError


class Job:
    """Kill-on-close job. Only the gated, newly created server joins this job."""

    def __init__(self):
        if os.name != 'nt':
            raise DataError('Window process containment requires Windows.')
        class Basic(ctypes.Structure):
            _fields_ = [('PerProcessUserTimeLimit', ctypes.c_int64), ('PerJobUserTimeLimit', ctypes.c_int64),
                        ('LimitFlags', wintypes.DWORD), ('MinimumWorkingSetSize', ctypes.c_size_t),
                        ('MaximumWorkingSetSize', ctypes.c_size_t), ('ActiveProcessLimit', wintypes.DWORD),
                        ('Affinity', ctypes.c_size_t), ('PriorityClass', wintypes.DWORD),
                        ('SchedulingClass', wintypes.DWORD)]
        class IO(ctypes.Structure):
            _fields_ = [(name, ctypes.c_uint64) for name in
                        ('ReadOperationCount', 'WriteOperationCount', 'OtherOperationCount',
                         'ReadTransferCount', 'WriteTransferCount', 'OtherTransferCount')]
        class Extended(ctypes.Structure):
            _fields_ = [('BasicLimitInformation', Basic), ('IoInfo', IO),
                        ('ProcessMemoryLimit', ctypes.c_size_t), ('JobMemoryLimit', ctypes.c_size_t),
                        ('PeakProcessMemoryUsed', ctypes.c_size_t), ('PeakJobMemoryUsed', ctypes.c_size_t)]
        self.api = ctypes.WinDLL('kernel32', use_last_error=True)
        declarations = {
            'CreateJobObjectW': ([ctypes.c_void_p, wintypes.LPCWSTR], wintypes.HANDLE),
            'SetInformationJobObject': ([wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD], wintypes.BOOL),
            'AssignProcessToJobObject': ([wintypes.HANDLE, wintypes.HANDLE], wintypes.BOOL),
            'TerminateJobObject': ([wintypes.HANDLE, wintypes.UINT], wintypes.BOOL),
            'QueryInformationJobObject': ([wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p], wintypes.BOOL),
            'CloseHandle': ([wintypes.HANDLE], wintypes.BOOL),
        }
        for name, (args, result) in declarations.items():
            function = getattr(self.api, name)
            function.argtypes, function.restype = args, result
        self.handle = self.api.CreateJobObjectW(None, None)  # Non-inheritable; never copied into the child.
        if not self.handle:
            raise ctypes.WinError(ctypes.get_last_error())
        limits = Extended()
        limits.BasicLimitInformation.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not self.api.SetInformationJobObject(self.handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
            error = ctypes.WinError(ctypes.get_last_error())
            self.close()
            raise error

    def assign(self, child):
        # Popen owns this live process handle. A PID lookup would reintroduce reuse races.
        if not self.api.AssignProcessToJobObject(self.handle, int(child._handle)):
            raise ctypes.WinError(ctypes.get_last_error())

    def drain(self):
        if not self.api.TerminateJobObject(self.handle, 1):
            raise ctypes.WinError(ctypes.get_last_error())
        class Accounting(ctypes.Structure):
            _fields_ = [(name, ctypes.c_int64) for name in
                        ('TotalUserTime', 'TotalKernelTime', 'ThisPeriodTotalUserTime', 'ThisPeriodTotalKernelTime')]
            _fields_ += [(name, wintypes.DWORD) for name in
                         ('TotalPageFaultCount', 'TotalProcesses', 'ActiveProcesses', 'TotalTerminatedProcesses')]
        deadline = time.monotonic() + 5
        while True:
            info = Accounting()
            if not self.api.QueryInformationJobObject(self.handle, 1, ctypes.byref(info), ctypes.sizeof(info), None):
                raise ctypes.WinError(ctypes.get_last_error())
            if not info.ActiveProcesses:
                return
            if time.monotonic() >= deadline:
                raise DataError('The owned server process tree did not stop in time.')
            time.sleep(.05)

    def close(self):
        if self.handle:
            self.api.CloseHandle(self.handle)
            self.handle = None


def gated_command(command: list[str]) -> list[str]:
    if getattr(sys, 'frozen', False):
        if command[1:2] != ['--internal-streamlit']:
            raise DataError('Unexpected frozen server command.')
        return [command[0], '--internal-window-server', *command[2:]]
    if command[1:3] != ['-m', 'streamlit']:
        raise DataError('Unexpected source server command.')
    return [command[0], '-m', 'portfolio_app.window', '--internal-window-server', *command[3:]]


@contextmanager
def contained_child(command: list[str], *, job_factory=Job, popen=subprocess.Popen):
    """The server cannot import Streamlit before job assignment succeeds."""
    job = job_factory()
    child = None
    try:
        child = popen(gated_command(command), stdin=subprocess.PIPE,
                      stdout=sys.stdout, stderr=sys.stderr,
                      creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0), close_fds=True)
        job.assign(child)
        child.stdin.write(b'start\n')
        child.stdin.flush()
        yield child
    finally:
        try:
            if child is not None:
                # EOF requests the child's installed Streamlit shutdown handler.
                try:
                    child.stdin.close()
                except OSError:
                    pass
                try:
                    child.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    child.terminate()
                    child.wait(timeout=5)
            job.drain()  # Also stops any descendants left after the direct child exited.
        finally:
            job.close()
