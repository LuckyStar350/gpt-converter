import ctypes
from pathlib import Path
import queue
import sys
import threading
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import windows_tray as tray_module


class FakeAPI:
    """Drive the real tray event loop without Windows or an Explorer session."""

    def __init__(self, *, fail_add=False, custom_icon=False,
                 fail_timer=False, recover_on_timer=True):
        self.user32 = self.kernel32 = self.shell32 = self
        self.queue = queue.Queue()
        self.calls = []
        self.fail_add = fail_add
        self.custom_icon = custom_icon
        self.fail_timer = fail_timer
        self.recover_on_timer = recover_on_timer
        self.proc = None
        self.menu_command = 0
        self.registered_again = threading.Event()
        self.add_count = 0

    def GetModuleHandleW(self, name):
        return 10

    def GetCurrentThreadId(self):
        return 20

    def RegisterWindowMessageW(self, name):
        return 0xC001

    def LoadImageW(self, *args):
        return 30 if self.custom_icon else 0

    def LoadIconW(self, *args):
        return 31

    def DestroyIcon(self, icon):
        self.calls.append(('destroy_icon', icon))
        return 1

    def RegisterClassW(self, value):
        self.proc = ctypes.cast(value, ctypes.POINTER(tray_module.WNDCLASSW)).contents.lpfnWndProc
        self.calls.append(('register_class',))
        return 1

    def UnregisterClassW(self, *args):
        self.calls.append(('unregister_class',))
        return 1

    def CreateWindowExW(self, *args):
        self.calls.append(('create_window',))
        return 40

    def DestroyWindow(self, hwnd):
        self.calls.append(('destroy_window', hwnd))
        self.proc(hwnd, tray_module.WM_DESTROY, 0, 0)
        return 1

    def DefWindowProcW(self, *args):
        return 0

    def Shell_NotifyIconW(self, operation, value):
        data = ctypes.cast(value, ctypes.POINTER(tray_module.NOTIFYICONDATAW)).contents
        self.calls.append(('notify', operation, data.hWnd, data.szTip))
        if operation == tray_module.NIM_ADD:
            self.add_count += 1
            if self.fail_add:
                return 0
            if self.add_count > 1:
                self.registered_again.set()
        return 1

    def GetMessageW(self, value, *args):
        message, wparam, lparam = self.queue.get(timeout=3)
        if message == tray_module.WM_QUIT:
            return 0
        if message == -1:
            return -1
        data = ctypes.cast(value, ctypes.POINTER(tray_module.MSG)).contents
        data.hwnd, data.message, data.wParam, data.lParam = 40, message, wparam, lparam
        return 1

    def TranslateMessage(self, value):
        return 1

    def DispatchMessageW(self, value):
        data = ctypes.cast(value, ctypes.POINTER(tray_module.MSG)).contents
        return self.proc(data.hwnd, data.message, data.wParam, data.lParam)

    def PostMessageW(self, hwnd, message, wparam, lparam):
        self.queue.put((message, wparam, lparam))
        return 1

    def PostThreadMessageW(self, thread_id, message, wparam, lparam):
        self.queue.put((message, wparam, lparam))
        return 1

    def PostQuitMessage(self, code):
        self.queue.put((tray_module.WM_QUIT, 0, 0))

    def CreatePopupMenu(self):
        self.calls.append(('create_menu',))
        return 50

    def AppendMenuW(self, menu, flags, command, label):
        self.calls.append(('menu_item', command, label))
        return 1

    def DestroyMenu(self, menu):
        self.calls.append(('destroy_menu',))
        return 1

    def GetCursorPos(self, value):
        data = ctypes.cast(value, ctypes.POINTER(tray_module.POINT)).contents
        data.x, data.y = 100, 200
        return 1

    def SetForegroundWindow(self, hwnd):
        return 1

    def TrackPopupMenu(self, *args):
        return self.menu_command

    def SetTimer(self, hwnd, timer, *args):
        self.calls.append(('set_timer', timer))
        if self.fail_timer:
            return 0
        # Explorer may initially be unavailable while rebuilding its taskbar.
        if self.recover_on_timer:
            self.fail_add = False
            self.queue.put((tray_module.WM_TIMER, timer, 0))
        else:
            for _ in range(tray_module.MAX_RECOVERY_ATTEMPTS):
                self.queue.put((tray_module.WM_TIMER, timer, 0))
        return timer

    def KillTimer(self, hwnd, timer):
        self.calls.append(('kill_timer', timer))
        return 1


class WindowsTrayTests(unittest.TestCase):
    def create_tray(self, api=None, on_open=None, on_exit=None, **options):
        api = api or FakeAPI()
        tray = tray_module.WindowsTray(on_open or (lambda: None), on_exit or (lambda: None),
                                       _api_factory=lambda: api, **options)
        self.addCleanup(tray.stop)
        return tray, api

    def test_registration_and_repeated_stop_release_all_owned_resources(self):
        tray, api = self.create_tray(FakeAPI(custom_icon=True), icon_path='icon.ico')
        self.assertIs(tray.start(), tray)
        self.assertEqual(tray.hwnd, 40)
        self.assertIn(('notify', tray_module.NIM_ADD, 40, 'GPT 转换工具'), api.calls)
        tray.stop()
        tray.stop()
        self.assertFalse(tray._thread.is_alive())
        self.assertEqual(tray.hwnd, 0)
        self.assertEqual(sum(call[0] == 'destroy_window' for call in api.calls), 1)
        self.assertEqual(sum(call[0] == 'unregister_class' for call in api.calls), 1)
        self.assertEqual(sum(call[:2] == ('notify', tray_module.NIM_DELETE) for call in api.calls), 1)
        self.assertIn(('destroy_icon', 30), api.calls)

    def test_shared_system_icon_is_not_destroyed(self):
        tray, api = self.create_tray()
        tray.start().stop()
        self.assertFalse(any(call[0] == 'destroy_icon' for call in api.calls))

    def test_normal_stop_does_not_request_service_exit(self):
        exit_calls = []
        tray, api = self.create_tray(on_exit=lambda: exit_calls.append('exit'))
        tray.start().stop()
        self.assertEqual(exit_calls, [])

    def test_start_failure_cleans_window_class_and_custom_icon(self):
        exit_calls = []
        tray, api = self.create_tray(FakeAPI(fail_add=True, custom_icon=True),
                                    on_exit=lambda: exit_calls.append('exit'), icon_path='icon.ico')
        with self.assertRaisesRegex(RuntimeError, 'Cannot create'):
            tray.start()
        self.assertFalse(tray._thread.is_alive())
        self.assertEqual(tray.hwnd, 0)
        self.assertIn(('destroy_window', 40), api.calls)
        self.assertIn(('unregister_class',), api.calls)
        self.assertIn(('destroy_icon', 30), api.calls)
        self.assertEqual(exit_calls, [])

    def test_double_click_opens_on_a_callback_thread(self):
        opened = threading.Event()
        callback_threads = []

        def on_open():
            callback_threads.append(threading.current_thread())
            opened.set()

        tray, api = self.create_tray(on_open=on_open)
        tray.start()
        api.PostMessageW(tray.hwnd, tray_module.WM_TRAY, 0,
                         (1 << 16) | tray_module.WM_LBUTTONDBLCLK)
        self.assertTrue(opened.wait(2))
        self.assertIsNot(callback_threads[0], tray._thread)

    def test_keyboard_selection_opens_tool(self):
        opened = threading.Event()
        tray, api = self.create_tray(on_open=opened.set)
        tray.start()
        api.PostMessageW(tray.hwnd, tray_module.WM_TRAY, 0,
                         (1 << 16) | tray_module.NIN_KEYSELECT)
        self.assertTrue(opened.wait(2))

    def test_right_click_menu_runs_selected_command_and_releases_menu(self):
        opened = threading.Event()
        tray, api = self.create_tray(on_open=opened.set)
        api.menu_command = tray_module.MENU_OPEN
        tray.start()
        api.PostMessageW(tray.hwnd, tray_module.WM_TRAY, 0, tray_module.WM_CONTEXTMENU)
        self.assertTrue(opened.wait(2))
        tray.stop()
        self.assertIn(('menu_item', tray_module.MENU_OPEN, '打开工具'), api.calls)
        self.assertIn(('menu_item', tray_module.MENU_EXIT, '退出工具'), api.calls)
        self.assertIn(('destroy_menu',), api.calls)

    def test_exit_callback_can_stop_tray_without_deadlocking(self):
        exited = threading.Event()
        calls = []

        def on_exit():
            calls.append('exit')
            tray.stop()
            exited.set()

        tray, api = self.create_tray(on_exit=on_exit)
        tray.start()
        api.PostMessageW(tray.hwnd, tray_module.WM_COMMAND, tray_module.MENU_EXIT, 0)
        self.assertTrue(exited.wait(2))
        tray._command(tray_module.MENU_EXIT)
        self.assertEqual(calls, ['exit'])
        self.assertFalse(tray._thread.is_alive())

    def test_explorer_restart_readds_icon_and_retries_if_shell_not_ready(self):
        tray, api = self.create_tray()
        tray.start()
        api.fail_add = True
        api.PostMessageW(tray.hwnd, tray._taskbar_created, 0, 0)
        self.assertTrue(api.registered_again.wait(2))
        tray.stop()
        self.assertEqual(api.add_count, 3)
        self.assertEqual(tray._restore_attempts, 0)
        self.assertIn(('set_timer', tray_module.RETRY_TIMER), api.calls)
        self.assertIn(('kill_timer', tray_module.RETRY_TIMER), api.calls)

    def test_recovery_timer_failure_requests_service_exit_once(self):
        exited = threading.Event()
        exit_calls = []

        def on_exit():
            exit_calls.append('exit')
            exited.set()

        tray, api = self.create_tray(FakeAPI(fail_timer=True), on_exit=on_exit)
        tray.start()
        api.fail_add = True
        with self.assertLogs('windows_tray', level='ERROR'):
            api.PostMessageW(tray.hwnd, tray._taskbar_created, 0, 0)
            self.assertTrue(exited.wait(2))
        tray.stop()
        self.assertEqual(exit_calls, ['exit'])
        self.assertEqual(api.add_count, 2)

    def test_recovery_is_bounded_and_stops_timer_before_requesting_exit(self):
        exited = threading.Event()
        exit_calls = []

        def on_exit():
            exit_calls.append((api.calls[-1], tray._restore_attempts))
            exited.set()

        tray, api = self.create_tray(FakeAPI(recover_on_timer=False), on_exit=on_exit)
        tray.start()
        api.fail_add = True
        with self.assertLogs('windows_tray', level='ERROR'):
            api.PostMessageW(tray.hwnd, tray._taskbar_created, 0, 0)
            self.assertTrue(exited.wait(2))
        tray.stop()
        self.assertEqual(api.add_count, 2 + tray_module.MAX_RECOVERY_ATTEMPTS)
        self.assertEqual(exit_calls, [
            (('kill_timer', tray_module.RETRY_TIMER), tray_module.MAX_RECOVERY_ATTEMPTS),
        ])

    def assert_unexpected_termination_requests_exit(self, message):
        exited = threading.Event()
        exit_calls = []

        def on_exit():
            exit_calls.append((tray.hwnd, threading.current_thread()))
            exited.set()

        tray, api = self.create_tray(on_exit=on_exit)
        tray.start()
        api.queue.put((message, 0, 0))
        self.assertTrue(exited.wait(2))
        tray.stop()
        self.assertFalse(tray._thread.is_alive())
        self.assertEqual(exit_calls[0][0], 0)
        self.assertIsNot(exit_calls[0][1], tray._thread)
        tray._command(tray_module.MENU_EXIT)
        self.assertEqual(len(exit_calls), 1)
        self.assertIn(('destroy_window', 40), api.calls)
        self.assertIn(('unregister_class',), api.calls)

    def test_unexpected_quit_cleans_icon_and_requests_service_exit_once(self):
        self.assert_unexpected_termination_requests_exit(tray_module.WM_QUIT)

    def test_message_loop_error_cleans_icon_and_requests_service_exit_once(self):
        with self.assertLogs('windows_tray', level='ERROR'):
            self.assert_unexpected_termination_requests_exit(-1)

    def test_unexpected_window_close_requests_service_exit(self):
        self.assert_unexpected_termination_requests_exit(tray_module.WM_CLOSE)

    def test_import_is_portable_and_native_start_rejects_non_windows(self):
        if sys.platform != 'win32':
            tray = tray_module.WindowsTray(lambda: None, lambda: None)
            with self.assertRaisesRegex(RuntimeError, 'requires Windows'):
                tray.start()
            tray.stop()


if __name__ == '__main__':
    unittest.main()
