"""Bounded native-panel automation and evidence for disposable hosted probes."""
from collections import deque
import ctypes as C
import json
import os
from pathlib import Path
import subprocess
import time


def hosted_input_enabled():
    return (os.environ.get('PORTFOLIO_TEST_HOSTED_INPUT') == '1'
            and os.environ.get('GITHUB_ACTIONS') == 'true'
            and os.environ.get('RUNNER_ENVIRONMENT') == 'github-hosted')


def enabled():
    return hosted_input_enabled() and os.environ.get('PORTFOLIO_TEST_PANEL_EVIDENCE') == '1'


class HostedPanelInput:
    def __init__(self):
        if not hosted_input_enabled():
            raise RuntimeError('Panel input requires an opted-in disposable hosted probe')
        self.cf = C.CDLL('/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation')
        self.ax = C.CDLL('/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices')
        signatures = {
            'CFStringCreateWithCString': ([C.c_void_p, C.c_char_p, C.c_uint32], C.c_void_p),
            'CFStringGetCString': ([C.c_void_p, C.c_void_p, C.c_long, C.c_uint32], C.c_bool),
            'CFRelease': ([C.c_void_p], None),
            'CFRetain': ([C.c_void_p], C.c_void_p),
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
        self.ax.AXUIElementPerformAction.argtypes = [C.c_void_p, C.c_void_p]
        self.ax.AXUIElementPerformAction.restype = C.c_int

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

    def button(self, window, title):
        # Breadth-first search reaches panel buttons before large file/sidebar
        # trees. Every queued reference is owned and released, including on error.
        pending = deque([(self.cf.CFRetain(window), 0)])
        visited = 0
        try:
            while pending and visited < 256:
                element, depth = pending.popleft()
                visited += 1
                try:
                    if (self.attribute(element, 'AXRole') == 'AXButton'
                            and self.attribute(element, 'AXTitle') == title
                            and self.attribute(element, 'AXEnabled') is True):
                        return self.cf.CFRetain(element)
                    if depth < 8:
                        def children(value):
                            if self.cf.CFGetTypeID(value) != self.cf.CFArrayGetTypeID():
                                return []
                            return [self.cf.CFRetain(self.cf.CFArrayGetValueAtIndex(value, i))
                                    for i in range(min(self.cf.CFArrayGetCount(value), 40))]
                        found = self.attribute(element, 'AXChildren', children)
                        if isinstance(found, list):
                            pending.extend((child, depth + 1) for child in found)
                finally:
                    self.cf.CFRelease(element)
        finally:
            for element, _ in pending:
                self.cf.CFRelease(element)
        raise RuntimeError(f'Enabled native {title} button not found')

    def press(self, app, title):
        if not hosted_input_enabled() or title not in {'Open', 'Save'}:
            raise RuntimeError('Only hosted synthetic Open/Save confirmation is permitted')
        def foreground():
            front = app.NSWorkspace.sharedWorkspace().frontmostApplication()
            if not front or front.processIdentifier() != os.getpid():
                raise RuntimeError('Synthetic app lost foreground ownership')
        foreground()
        if not self.ax.AXIsProcessTrusted():
            raise RuntimeError('Native panel automation requires accessibility permission')
        process = self.ax.AXUIElementCreateApplication(os.getpid())
        def confirm(window):
            if self.attribute(window, 'AXTitle') != title:
                raise RuntimeError('Focused accessibility window is not the expected file panel')
            button = self.button(window, title)
            action = self.cf.CFStringCreateWithCString(None, b'AXPress', 0x08000100)
            try:
                foreground()
                code = self.ax.AXUIElementPerformAction(button, action)
                # AX may time out waiting for modal callbacks although the click
                # was delivered. Never click twice: the probe must still observe
                # panel closure and verify the actual uploaded/downloaded bytes.
                if code not in {0, -25204}:  # kAXErrorCannotComplete
                    raise RuntimeError(f'Native button press failed: AX error {code}')
                return code
            finally:
                self.cf.CFRelease(action)
                self.cf.CFRelease(button)
        try:
            result = self.attribute(process, 'AXFocusedWindow', confirm)
            if isinstance(result, dict):
                raise RuntimeError(f'Focused file panel unavailable: {result}')
            return result
        finally:
            self.cf.CFRelease(process)


class PanelEvidence(HostedPanelInput):
    def __init__(self, output):
        if not enabled():
            raise RuntimeError('Panel evidence requires an opted-in disposable hosted probe')
        super().__init__()
        self.output = Path(output)
        self.output.mkdir(parents=True, exist_ok=True)
        self.records = []

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
