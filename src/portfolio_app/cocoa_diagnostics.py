"""Bounded native-panel evidence, only in opted-in disposable hosted probes."""
import ctypes as C
import json
import os
from pathlib import Path
import subprocess
import time


def enabled():
    return (os.environ.get('PORTFOLIO_TEST_PANEL_EVIDENCE') == '1'
            and os.environ.get('PORTFOLIO_TEST_HOSTED_INPUT') == '1'
            and os.environ.get('GITHUB_ACTIONS') == 'true'
            and os.environ.get('RUNNER_ENVIRONMENT') == 'github-hosted')


class PanelEvidence:
    def __init__(self, output):
        if not enabled():
            raise RuntimeError('Panel evidence requires an opted-in disposable hosted probe')
        self.output = Path(output)
        self.output.mkdir(parents=True, exist_ok=True)
        self.records = []
        self.cf = C.CDLL('/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation')
        self.ax = C.CDLL('/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices')
        signatures = {
            'CFStringCreateWithCString': ([C.c_void_p, C.c_char_p, C.c_uint32], C.c_void_p),
            'CFStringGetCString': ([C.c_void_p, C.c_void_p, C.c_long, C.c_uint32], C.c_bool),
            'CFRelease': ([C.c_void_p], None),
            'CFGetTypeID': ([C.c_void_p], C.c_ulong),
            'CFArrayGetCount': ([C.c_void_p], C.c_long),
            'CFArrayGetValueAtIndex': ([C.c_void_p, C.c_long], C.c_void_p),
            'CFBooleanGetValue': ([C.c_void_p], C.c_bool),
            'CFCopyDescription': ([C.c_void_p], C.c_void_p),
        }
        for name, (args, result) in signatures.items():
            function = getattr(self.cf, name)
            function.argtypes, function.restype = args, result
        for name in ['CFStringGetTypeID', 'CFBooleanGetTypeID', 'CFArrayGetTypeID']:
            getattr(self.cf, name).restype = C.c_ulong
        self.ax.AXUIElementCreateSystemWide.restype = C.c_void_p
        self.ax.AXUIElementSetMessagingTimeout.argtypes = [C.c_void_p, C.c_float]
        system = self.ax.AXUIElementCreateSystemWide()
        try:
            self.ax.AXUIElementSetMessagingTimeout(system, .05)
        finally:
            self.cf.CFRelease(system)
        self.ax.AXIsProcessTrusted.restype = C.c_bool
        self.ax.AXUIElementCreateApplication.argtypes = [C.c_int]
        self.ax.AXUIElementCreateApplication.restype = C.c_void_p
        self.ax.AXUIElementCopyAttributeValue.argtypes = [C.c_void_p, C.c_void_p, C.POINTER(C.c_void_p)]
        self.ax.AXUIElementCopyAttributeValue.restype = C.c_int

    def text(self, value):
        buffer = C.create_string_buffer(4096)
        if self.cf.CFStringGetCString(value, buffer, len(buffer), 0x08000100):
            return buffer.value.decode('utf-8', errors='replace')
        return '<unavailable>'

    def attribute(self, element, name, transform=None):
        key = self.cf.CFStringCreateWithCString(None, name.encode(), 0x08000100)
        value = C.c_void_p()
        try:
            error = self.ax.AXUIElementCopyAttributeValue(element, key, C.byref(value))
            if error:
                return {'ax_error': error}
            if transform:
                return transform(value)
            kind = self.cf.CFGetTypeID(value)
            if kind == self.cf.CFStringGetTypeID():
                return self.text(value)
            if kind == self.cf.CFBooleanGetTypeID():
                return bool(self.cf.CFBooleanGetValue(value))
            description = self.cf.CFCopyDescription(value)
            try:
                return self.text(description)
            finally:
                self.cf.CFRelease(description)
        finally:
            self.cf.CFRelease(key)
            if value:
                self.cf.CFRelease(value)

    def node(self, element):
        return {name: self.attribute(element, name) for name in
                ['AXRole', 'AXTitle', 'AXDescription', 'AXValue', 'AXEnabled', 'AXFocused']}

    def tree(self, element, depth=0, budget=None):
        budget = [40] if budget is None else budget
        budget[0] -= 1
        result = self.node(element)
        if depth < 6 and budget[0] > 0:
            def children(value):
                if self.cf.CFGetTypeID(value) != self.cf.CFArrayGetTypeID():
                    return []
                return [self.tree(self.cf.CFArrayGetValueAtIndex(value, index), depth + 1, budget)
                        for index in range(min(self.cf.CFArrayGetCount(value), 30)) if budget[0] > 0]
            result['children'] = self.attribute(element, 'AXChildren', children)
        return result

    def record(self, label, app, panel, *, screenshot=False):
        # Never inspect a different foreground app, even on the disposable host.
        front = app.NSWorkspace.sharedWorkspace().frontmostApplication()
        if not front or front.processIdentifier() != os.getpid():
            self.records.append({'label': label, 'error': 'Synthetic app is not foreground'})
        else:
            import Quartz as Q
            row = {'label': label, 'time': time.monotonic(),
                   'accessibility_trusted': bool(self.ax.AXIsProcessTrusted()),
                   'post_event_access': bool(Q.CGPreflightPostEventAccess()),
                   'screen_capture_access': bool(Q.CGPreflightScreenCaptureAccess())}
            process = self.ax.AXUIElementCreateApplication(os.getpid())
            try:
                row['focus'] = self.attribute(process, 'AXFocusedUIElement', self.node)
                def window(value):
                    return {'window': self.node(value),
                            'default_button': self.attribute(value, 'AXDefaultButton', self.node)}
                row['window'] = self.attribute(process, 'AXFocusedWindow', window)
                if screenshot:
                    row['tree'] = self.tree(process)
                    if panel is not None:
                        target = self.output / f'{len(self.records):03d}-panel.png'
                        captured = subprocess.run(['screencapture', '-x', '-o', '-l', str(panel.windowNumber()), str(target)],
                                                  capture_output=True, text=True, timeout=5)
                        row['capture'] = {'exit': captured.returncode, 'error': captured.stderr}
            finally:
                self.cf.CFRelease(process)
            self.records.append(row)
        (self.output / 'panel-evidence.json').write_text(json.dumps(self.records, indent=2), encoding='utf-8')
