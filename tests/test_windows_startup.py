import ctypes
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import windows_startup as startup


class FakeAPI:
    def __init__(self, wait_result=startup.WAIT_OBJECT_0):
        # A handle beyond 32 bits catches accidental HANDLE narrowing.
        self.handle = 0x123456789ABC
        self.kernel32 = SimpleNamespace(
            CreateMutexW=Mock(return_value=self.handle),
            WaitForSingleObject=Mock(return_value=wait_result),
            ReleaseMutex=Mock(return_value=True),
            CloseHandle=Mock(return_value=True),
        )


class StartupLockTests(unittest.TestCase):
    def make_lock(self, wait_result=startup.WAIT_OBJECT_0):
        api = FakeAPI(wait_result)
        factory = Mock(return_value=api)
        lock = startup.StartupLock('Local\\gpt-converter-test', _api_factory=factory)
        return lock, api, factory

    def test_dll_is_loaded_only_during_acquire(self):
        lock, api, factory = self.make_lock()
        factory.assert_not_called()
        lock.release()
        factory.assert_not_called()
        lock.acquire()
        factory.assert_called_once_with()
        lock.release()

    def test_non_windows_is_a_noop_without_loading_dll(self):
        with patch.object(startup.sys, 'platform', 'linux'):
            with patch.object(startup, '_Win32API') as factory:
                lock = startup.StartupLock('Local\\gpt-converter-test')
                lock.acquire()
                lock.release()
                factory.assert_not_called()

    def test_api_has_explicit_pointer_safe_signatures(self):
        library = SimpleNamespace(**{
            name: Mock() for name in (
                'CreateMutexW', 'WaitForSingleObject', 'ReleaseMutex', 'CloseHandle',
            )
        })
        with patch.object(startup.ctypes, 'WinDLL', return_value=library, create=True) as load:
            startup._Win32API()
        load.assert_called_once_with('kernel32', use_last_error=True)
        self.assertEqual(library.CreateMutexW.argtypes,
                         [ctypes.c_void_p, ctypes.c_int32, ctypes.c_wchar_p])
        self.assertIs(library.CreateMutexW.restype, ctypes.c_void_p)
        self.assertEqual(library.WaitForSingleObject.argtypes,
                         [ctypes.c_void_p, ctypes.c_uint32])
        self.assertIs(library.WaitForSingleObject.restype, ctypes.c_uint32)
        for name in ('ReleaseMutex', 'CloseHandle'):
            self.assertEqual(getattr(library, name).argtypes, [ctypes.c_void_p])
            self.assertIs(getattr(library, name).restype, ctypes.c_int32)

    def test_acquire_does_not_recursively_own_mutex_and_release_is_idempotent(self):
        lock, api, _ = self.make_lock()
        lock.acquire()
        lock.acquire()
        api.kernel32.CreateMutexW.assert_called_once_with(None, False, lock.name)
        api.kernel32.WaitForSingleObject.assert_called_once_with(api.handle, 15000)
        lock.release()
        lock.release()
        api.kernel32.ReleaseMutex.assert_called_once_with(api.handle)
        api.kernel32.CloseHandle.assert_called_once_with(api.handle)

    def test_abandoned_mutex_is_owned_and_released(self):
        lock, api, _ = self.make_lock(startup.WAIT_ABANDONED)
        lock.acquire()
        lock.release()
        api.kernel32.ReleaseMutex.assert_called_once_with(api.handle)
        api.kernel32.CloseHandle.assert_called_once_with(api.handle)

    def test_timeout_closes_handle_and_allows_a_later_attempt(self):
        lock, api, _ = self.make_lock(startup.WAIT_TIMEOUT)
        with self.assertRaisesRegex(RuntimeError, 'Timed out after 15000 ms'):
            lock.acquire()
        lock.release()
        api.kernel32.ReleaseMutex.assert_not_called()
        api.kernel32.CloseHandle.assert_called_once_with(api.handle)
        api.kernel32.WaitForSingleObject.return_value = startup.WAIT_OBJECT_0
        lock.acquire()
        lock.release()
        self.assertEqual(api.kernel32.CloseHandle.call_count, 2)

    def test_create_failure_does_not_wait_or_close_an_invalid_handle(self):
        lock, api, _ = self.make_lock()
        api.kernel32.CreateMutexW.return_value = 0
        with patch.object(startup.ctypes, 'get_last_error', return_value=5, create=True):
            with self.assertRaisesRegex(RuntimeError, 'CreateMutexW failed \\(error 5\\)'):
                lock.acquire()
        api.kernel32.WaitForSingleObject.assert_not_called()
        api.kernel32.CloseHandle.assert_not_called()

    def test_wait_failure_preserves_windows_error_and_closes_handle(self):
        lock, api, _ = self.make_lock(startup.WAIT_FAILED)
        with patch.object(startup.ctypes, 'get_last_error', return_value=6, create=True):
            with self.assertRaisesRegex(RuntimeError, 'WaitForSingleObject failed \\(error 6\\)'):
                lock.acquire()
        lock.release()
        api.kernel32.ReleaseMutex.assert_not_called()
        api.kernel32.CloseHandle.assert_called_once_with(api.handle)

    def test_wait_exception_and_unexpected_result_also_close_handle(self):
        for result in (3, RuntimeError('simulated wait failure')):
            with self.subTest(result=result):
                lock, api, _ = self.make_lock(result)
                if isinstance(result, BaseException):
                    api.kernel32.WaitForSingleObject.side_effect = result
                with self.assertRaises(RuntimeError):
                    lock.acquire()
                api.kernel32.CloseHandle.assert_called_once_with(api.handle)
                api.kernel32.ReleaseMutex.assert_not_called()

    def test_release_failure_still_closes_handle_and_remains_idempotent(self):
        for result in (False, RuntimeError('simulated release failure')):
            with self.subTest(result=result):
                lock, api, _ = self.make_lock()
                lock.acquire()
                if isinstance(result, BaseException):
                    api.kernel32.ReleaseMutex.side_effect = result
                else:
                    api.kernel32.ReleaseMutex.return_value = result
                with self.assertRaises(RuntimeError):
                    lock.release()
                lock.release()
                api.kernel32.CloseHandle.assert_called_once_with(api.handle)

    def test_close_failure_is_reported_without_duplicate_release(self):
        lock, api, _ = self.make_lock()
        lock.acquire()
        api.kernel32.CloseHandle.return_value = False
        with self.assertRaisesRegex(RuntimeError, 'CloseHandle failed'):
            lock.release()
        lock.release()
        api.kernel32.ReleaseMutex.assert_called_once_with(api.handle)
        api.kernel32.CloseHandle.assert_called_once_with(api.handle)

    def test_cleanup_failure_does_not_hide_wait_failure(self):
        lock, api, _ = self.make_lock(startup.WAIT_TIMEOUT)
        api.kernel32.CloseHandle.return_value = False
        with self.assertRaisesRegex(RuntimeError, 'Timed out') as caught:
            lock.acquire()
        self.assertIn('CloseHandle failed', caught.exception.__notes__[0])

    def test_name_and_bounded_timeout_are_validated(self):
        for name in ('', None, 'bad\0name'):
            with self.subTest(name=name), self.assertRaises(ValueError):
                startup.StartupLock(name)
        for timeout in (-1, startup.WAIT_FAILED, 1.5, True):
            with self.subTest(timeout=timeout), self.assertRaises(ValueError):
                startup.StartupLock('test', timeout)


if __name__ == '__main__':
    unittest.main()
