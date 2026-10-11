"""Windows notification-area icon, using only the Python standard library.

WindowsTray.start() waits until the icon has been registered. Callbacks run on
separate threads, so an exit callback may safely shut down an HTTP server.
WindowsTray.stop() removes the icon and waits for its message thread to finish.
Importing this module on other operating systems is supported.
"""
from __future__ import annotations

import ctypes
import logging
from pathlib import Path
import sys
import threading
import uuid


LOGGER = logging.getLogger(__name__)
MENU_OPEN = 1001
MENU_EXIT = 1002
WM_DESTROY = 0x0002
WM_CLOSE = 0x0010
WM_COMMAND = 0x0111
WM_TIMER = 0x0113
WM_QUIT = 0x0012
WM_CONTEXTMENU = 0x007B
WM_LBUTTONDBLCLK = 0x0203
WM_RBUTTONUP = 0x0205
NIN_KEYSELECT = 0x0401
WM_TRAY = 0x8001
NIM_ADD, NIM_DELETE, NIM_SETVERSION = 0, 2, 4
NIF_MESSAGE, NIF_ICON, NIF_TIP, NIF_SHOWTIP = 1, 2, 4, 0x80
RETRY_TIMER = 1
MAX_RECOVERY_ATTEMPTS = 10
RECOVERY_INTERVAL_MS = 1000

# Win32 uses LLP64: DWORD/LONG stay 32-bit, even in a 64-bit process.
DWORD = ctypes.c_uint32
LONG = ctypes.c_int32
UINT = ctypes.c_uint32
HANDLE = ctypes.c_void_p
WPARAM = ctypes.c_size_t
LPARAM = ctypes.c_ssize_t
LRESULT = ctypes.c_ssize_t
WNDPROC = getattr(ctypes, 'WINFUNCTYPE', ctypes.CFUNCTYPE)(
    LRESULT, HANDLE, UINT, WPARAM, LPARAM,
)


class POINT(ctypes.Structure):
    _fields_ = [('x', LONG), ('y', LONG)]


class MSG(ctypes.Structure):
    _fields_ = [
        ('hwnd', HANDLE), ('message', UINT), ('wParam', WPARAM),
        ('lParam', LPARAM), ('time', DWORD), ('pt', POINT),
        ('lPrivate', DWORD),
    ]


class WNDCLASSW(ctypes.Structure):
    _fields_ = [
        ('style', UINT), ('lpfnWndProc', WNDPROC), ('cbClsExtra', LONG),
        ('cbWndExtra', LONG), ('hInstance', HANDLE), ('hIcon', HANDLE),
        ('hCursor', HANDLE), ('hbrBackground', HANDLE),
        ('lpszMenuName', ctypes.c_wchar_p), ('lpszClassName', ctypes.c_wchar_p),
    ]


class GUID(ctypes.Structure):
    _fields_ = [
        ('Data1', DWORD), ('Data2', ctypes.c_uint16),
        ('Data3', ctypes.c_uint16), ('Data4', ctypes.c_ubyte * 8),
    ]


class NOTIFYICONDATAW(ctypes.Structure):
    _fields_ = [
        ('cbSize', DWORD), ('hWnd', HANDLE), ('uID', UINT), ('uFlags', UINT),
        ('uCallbackMessage', UINT), ('hIcon', HANDLE),
        ('szTip', ctypes.c_wchar * 128), ('dwState', DWORD),
        ('dwStateMask', DWORD), ('szInfo', ctypes.c_wchar * 256),
        ('uVersion', UINT), ('szInfoTitle', ctypes.c_wchar * 64),
        ('dwInfoFlags', DWORD), ('guidItem', GUID), ('hBalloonIcon', HANDLE),
    ]


def _error(message):
    code = ctypes.get_last_error() if hasattr(ctypes, 'get_last_error') else 0
    return OSError(code, message)


class _Win32API:
    """Typed DLL boundary; tests can substitute this without a desktop session."""

    def __init__(self):
        self.user32 = ctypes.WinDLL('user32', use_last_error=True)
        self.kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
        self.shell32 = ctypes.WinDLL('shell32', use_last_error=True)
        pointer = ctypes.POINTER
        wchar = ctypes.c_wchar_p
        specs = {
            'RegisterClassW': ([pointer(WNDCLASSW)], ctypes.c_uint16),
            'UnregisterClassW': ([wchar, HANDLE], LONG),
            'CreateWindowExW': ([DWORD, wchar, wchar, DWORD, LONG, LONG, LONG,
                                LONG, HANDLE, HANDLE, HANDLE, HANDLE], HANDLE),
            'DestroyWindow': ([HANDLE], LONG),
            'DefWindowProcW': ([HANDLE, UINT, WPARAM, LPARAM], LRESULT),
            'GetMessageW': ([pointer(MSG), HANDLE, UINT, UINT], LONG),
            'TranslateMessage': ([pointer(MSG)], LONG),
            'DispatchMessageW': ([pointer(MSG)], LRESULT),
            'PostMessageW': ([HANDLE, UINT, WPARAM, LPARAM], LONG),
            'PostThreadMessageW': ([DWORD, UINT, WPARAM, LPARAM], LONG),
            'PostQuitMessage': ([LONG], None),
            'RegisterWindowMessageW': ([wchar], UINT),
            'LoadImageW': ([HANDLE, wchar, UINT, LONG, LONG, UINT], HANDLE),
            'LoadIconW': ([HANDLE, wchar], HANDLE),
            'DestroyIcon': ([HANDLE], LONG),
            'CreatePopupMenu': ([], HANDLE),
            'AppendMenuW': ([HANDLE, UINT, WPARAM, wchar], LONG),
            'TrackPopupMenu': ([HANDLE, UINT, LONG, LONG, LONG, HANDLE, HANDLE], UINT),
            'DestroyMenu': ([HANDLE], LONG),
            'GetCursorPos': ([pointer(POINT)], LONG),
            'SetForegroundWindow': ([HANDLE], LONG),
            'SetTimer': ([HANDLE, WPARAM, UINT, HANDLE], WPARAM),
            'KillTimer': ([HANDLE, WPARAM], LONG),
        }
        for name, (arguments, result) in specs.items():
            function = getattr(self.user32, name)
            function.argtypes, function.restype = arguments, result
        self.kernel32.GetModuleHandleW.argtypes = [wchar]
        self.kernel32.GetModuleHandleW.restype = HANDLE
        self.kernel32.GetCurrentThreadId.argtypes = []
        self.kernel32.GetCurrentThreadId.restype = DWORD
        self.shell32.Shell_NotifyIconW.argtypes = [DWORD, pointer(NOTIFYICONDATAW)]
        self.shell32.Shell_NotifyIconW.restype = LONG


class WindowsTray:
    def __init__(self, on_open, on_exit, icon_path=None, tooltip='GPT 转换工具',
                 *, _api_factory=None):
        if not callable(on_open) or not callable(on_exit):
            raise TypeError('Tray callbacks must be callable')
        self.on_open, self.on_exit = on_open, on_exit
        self.icon_path = Path(icon_path).resolve() if icon_path else None
        self.tooltip = tooltip[:127]
        self._api_factory = _api_factory or _Win32API
        self._supported = sys.platform == 'win32' or _api_factory is not None
        self._lock = threading.Lock()
        self._ready = threading.Event()
        self._stop_requested = threading.Event()
        self._exit_requested = threading.Event()
        self._thread = None
        self._thread_id = 0
        self._hwnd = 0
        self._failure = None
        self._api = None
        self._icon = None
        self._owns_icon = False
        self._registered = False
        self._restore_attempts = 0
        self._taskbar_created = 0
        self._wnd_proc = None

    @property
    def hwnd(self):
        """Window handle for diagnostics; zero before start or after stop."""
        return self._hwnd

    def start(self):
        if not self._supported:
            raise RuntimeError('The notification-area icon requires Windows')
        with self._lock:
            if self._thread is None or not self._thread.is_alive():
                self._ready.clear()
                self._stop_requested.clear()
                self._exit_requested.clear()
                self._failure = None
                self._thread = threading.Thread(
                    target=self._run, name='gpt-converter-tray', daemon=True,
                )
                self._thread.start()
        if not self._ready.wait(10):
            self.stop()
            raise RuntimeError('Timed out creating the notification-area icon')
        if self._failure is not None:
            self.stop()
            raise RuntimeError('Cannot create the notification-area icon') from self._failure
        if not self._registered or not self._hwnd:
            self.stop()
            raise RuntimeError('The notification-area icon stopped during startup')
        return self

    def stop(self):
        """Remove the icon and wait for window/resource cleanup; safe to repeat."""
        with self._lock:
            self._stop_requested.set()
            thread, hwnd, thread_id = self._thread, self._hwnd, self._thread_id
            api = self._api
        if thread is None or not thread.is_alive():
            return
        if hwnd and not api.user32.PostMessageW(hwnd, WM_CLOSE, 0, 0):
            # A destroyed window can race with this request; WM_QUIT also wakes
            # the message loop and the finally block still removes all resources.
            if not api.user32.PostThreadMessageW(thread_id, WM_QUIT, 0, 0):
                if thread.is_alive():
                    raise _error('Cannot stop the notification-area message thread')
        if thread is not threading.current_thread():
            thread.join()

    def _notify(self, operation):
        data = NOTIFYICONDATAW()
        data.cbSize = ctypes.sizeof(data)
        data.hWnd = self._hwnd
        data.uID = 1
        data.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP | NIF_SHOWTIP
        data.uCallbackMessage = WM_TRAY
        data.hIcon = self._icon
        data.szTip = self.tooltip
        data.uVersion = 4
        return bool(self._api.shell32.Shell_NotifyIconW(operation, ctypes.byref(data)))

    def _add_icon(self):
        if not self._notify(NIM_ADD):
            return False
        self._registered = True
        # Older shells can reject v4 while retaining a working icon.
        self._notify(NIM_SETVERSION)
        return True

    def _remove_icon(self):
        if self._registered:
            self._notify(NIM_DELETE)
            self._registered = False

    def _dispatch_callback(self, callback, *, exiting=False):
        if exiting:
            if self._exit_requested.is_set():
                return
            self._exit_requested.set()

        def invoke():
            try:
                callback()
            except Exception:
                LOGGER.exception('Notification-area action failed')

        threading.Thread(target=invoke, name='gpt-converter-tray-action', daemon=True).start()

    def _command(self, command):
        if command == MENU_OPEN:
            self._dispatch_callback(self.on_open)
        elif command == MENU_EXIT:
            self._dispatch_callback(self.on_exit, exiting=True)

    def _show_menu(self, hwnd):
        user32 = self._api.user32
        menu = user32.CreatePopupMenu()
        if not menu:
            raise _error('Cannot create the notification-area menu')
        try:
            if not user32.AppendMenuW(menu, 0, MENU_OPEN, '打开工具'):
                raise _error('Cannot add the open menu item')
            if not user32.AppendMenuW(menu, 0, MENU_EXIT, '退出工具'):
                raise _error('Cannot add the exit menu item')
            point = POINT()
            if not user32.GetCursorPos(ctypes.byref(point)):
                raise _error('Cannot locate the notification-area menu')
            user32.SetForegroundWindow(hwnd)
            command = user32.TrackPopupMenu(menu, 0x100 | 0x2, point.x, point.y,
                                           0, hwnd, None)
            # The final WM_NULL lets Windows dismiss the menu on a later click.
            user32.PostMessageW(hwnd, 0, 0, 0)
            self._command(command)
        finally:
            user32.DestroyMenu(menu)

    def _window_proc(self, hwnd, message, wparam, lparam):
        user32 = self._api.user32
        try:
            if message == WM_TRAY:
                event = lparam & 0xFFFF  # v4 stores the icon ID in the high word.
                if event in (WM_LBUTTONDBLCLK, NIN_KEYSELECT):
                    self._dispatch_callback(self.on_open)
                elif event in (WM_RBUTTONUP, WM_CONTEXTMENU):
                    self._show_menu(hwnd)
                return 0
            if message == WM_COMMAND:
                self._command(wparam & 0xFFFF)
                return 0
            if message == self._taskbar_created:
                self._registered = False
                self._restore_attempts = 0
                user32.KillTimer(hwnd, RETRY_TIMER)
                if not self._add_icon():
                    if not user32.SetTimer(hwnd, RETRY_TIMER, RECOVERY_INTERVAL_MS, None):
                        LOGGER.error('Cannot start notification-area icon recovery')
                        self._dispatch_callback(self.on_exit, exiting=True)
                return 0
            if message == WM_TIMER and wparam == RETRY_TIMER:
                if self._add_icon():
                    self._restore_attempts = 0
                    user32.KillTimer(hwnd, RETRY_TIMER)
                else:
                    self._restore_attempts += 1
                    if self._restore_attempts >= MAX_RECOVERY_ATTEMPTS:
                        user32.KillTimer(hwnd, RETRY_TIMER)
                        LOGGER.error('Cannot restore the notification-area icon')
                        self._dispatch_callback(self.on_exit, exiting=True)
                return 0
            if message == WM_CLOSE:
                user32.DestroyWindow(hwnd)
                return 0
            if message == WM_DESTROY:
                self._remove_icon()
                user32.KillTimer(hwnd, RETRY_TIMER)
                self._hwnd = 0
                user32.PostQuitMessage(0)
                return 0
        except Exception:
            # Exceptions must not cross a ctypes callback into the Windows loop.
            LOGGER.exception('Notification-area window event failed')
        return user32.DefWindowProcW(hwnd, message, wparam, lparam)

    def _run(self):
        class_name = f'GPTConverterTray_{uuid.uuid4().hex}'
        class_registered = False
        startup_succeeded = False
        instance = None
        try:
            self._api = self._api_factory()
            user32 = self._api.user32
            self._thread_id = self._api.kernel32.GetCurrentThreadId()
            if self._stop_requested.is_set():
                return
            instance = self._api.kernel32.GetModuleHandleW(None)
            self._taskbar_created = user32.RegisterWindowMessageW('TaskbarCreated')
            if not self._taskbar_created:
                raise _error('Cannot register Explorer restart notification')
            if self.icon_path:
                self._icon = user32.LoadImageW(None, str(self.icon_path), 1, 0, 0, 0x10 | 0x40)
                self._owns_icon = bool(self._icon)
            if not self._icon:
                application_icon = ctypes.cast(ctypes.c_void_p(32512), ctypes.c_wchar_p)
                self._icon = user32.LoadIconW(None, application_icon)
            if not self._icon:
                raise _error('Cannot load the notification-area icon')
            self._wnd_proc = WNDPROC(self._window_proc)
            window_class = WNDCLASSW()
            window_class.lpfnWndProc = self._wnd_proc
            window_class.hInstance = instance
            window_class.lpszClassName = class_name
            if not user32.RegisterClassW(ctypes.byref(window_class)):
                raise _error('Cannot register the notification-area window')
            class_registered = True
            # A hidden top-level window receives Explorer's TaskbarCreated
            # broadcast; a message-only window would miss that broadcast.
            self._hwnd = user32.CreateWindowExW(0, class_name, self.tooltip, 0,
                                              0, 0, 0, 0, None, None, instance, None)
            if not self._hwnd:
                raise _error('Cannot create the notification-area window')
            if self._stop_requested.is_set():
                return
            if not self._add_icon():
                raise _error('Shell_NotifyIconW could not register the icon')
            startup_succeeded = True
            self._ready.set()
            message = MSG()
            while not self._stop_requested.is_set():
                result = user32.GetMessageW(ctypes.byref(message), None, 0, 0)
                if result == -1:
                    raise _error('The notification-area message loop failed')
                if result == 0:
                    break
                user32.TranslateMessage(ctypes.byref(message))
                user32.DispatchMessageW(ctypes.byref(message))
        except Exception as exc:
            self._failure = exc
            if self._ready.is_set():
                LOGGER.exception('The notification-area message thread failed')
        finally:
            if self._api is not None:
                self._remove_icon()
                if self._hwnd:
                    self._api.user32.DestroyWindow(self._hwnd)
                    self._hwnd = 0
                if class_registered:
                    self._api.user32.UnregisterClassW(class_name, instance)
                if self._owns_icon:
                    self._api.user32.DestroyIcon(self._icon)
            self._icon = None
            self._owns_icon = False
            self._thread_id = 0
            self._wnd_proc = None
            self._ready.set()
            if startup_succeeded and not self._stop_requested.is_set():
                # A failed message loop or unexpectedly destroyed window must
                # not leave an HTTP service running without an exit control.
                self._dispatch_callback(self.on_exit, exiting=True)
