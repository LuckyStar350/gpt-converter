"""A short-lived Windows mutex for serializing service startup.

Acquire and release on the same thread. Release once the server can respond to
the next launcher's health probe, and also release in the launcher's finally
block. The mutex is deliberately not held for the service's whole lifetime.
Other operating systems do not need this Windows launcher coordination.
"""
from __future__ import annotations

import ctypes
import sys


HANDLE = ctypes.c_void_p
BOOL = ctypes.c_int32
DWORD = ctypes.c_uint32
WAIT_OBJECT_0 = 0x00000000
WAIT_ABANDONED = 0x00000080
WAIT_TIMEOUT = 0x00000102
WAIT_FAILED = 0xFFFFFFFF


def _error(operation):
    code = ctypes.get_last_error() if hasattr(ctypes, 'get_last_error') else 0
    return RuntimeError(f'Win32 {operation} failed (error {code})')


class _Win32API:
    """Load the DLL only when acquiring, with pointer-safe Win32 signatures."""

    def __init__(self):
        self.kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
        specs = {
            'CreateMutexW': ([HANDLE, BOOL, ctypes.c_wchar_p], HANDLE),
            'WaitForSingleObject': ([HANDLE, DWORD], DWORD),
            'ReleaseMutex': ([HANDLE], BOOL),
            'CloseHandle': ([HANDLE], BOOL),
        }
        for name, (arguments, result) in specs.items():
            function = getattr(self.kernel32, name)
            function.argtypes, function.restype = arguments, result


class StartupLock:
    """Coordinate startup using a named mutex (for example Local\\project-id).

    The name is passed unchanged to Windows. Repeated acquire calls while held
    do not acquire recursively; release is safe to call more than once.
    ``_api_factory`` lets tests exercise the Win32 contract without Windows.
    """

    def __init__(self, name, timeout_ms=15000, *, _api_factory=None):
        if not isinstance(name, str) or not name or '\0' in name:
            raise ValueError('Startup lock name must be a nonempty string without NUL')
        if isinstance(timeout_ms, bool) or not isinstance(timeout_ms, int):
            raise ValueError('Startup lock timeout must be an integer in milliseconds')
        if not 0 <= timeout_ms < WAIT_FAILED:
            raise ValueError('Startup lock timeout must be between 0 and 4294967294 ms')
        self.name = name
        self.timeout_ms = timeout_ms
        self._supported = sys.platform == 'win32' or _api_factory is not None
        self._api_factory = _api_factory or _Win32API
        self._api = None
        self._handle = None

    def _close(self, handle):
        if not self._api.kernel32.CloseHandle(handle):
            raise _error('CloseHandle')

    def acquire(self):
        if not self._supported or self._handle is not None:
            return
        if self._api is None:
            self._api = self._api_factory()
        handle = self._api.kernel32.CreateMutexW(None, False, self.name)
        if not handle:
            raise _error('CreateMutexW')
        try:
            result = self._api.kernel32.WaitForSingleObject(handle, self.timeout_ms)
            # An abandoned mutex is owned by this thread after the wait, too.
            if result in (WAIT_OBJECT_0, WAIT_ABANDONED):
                self._handle = handle
                return
            if result == WAIT_TIMEOUT:
                raise RuntimeError(
                    f'Timed out after {self.timeout_ms} ms waiting for startup '
                    f'lock {self.name!r}; another instance may still be starting'
                )
            if result == WAIT_FAILED:
                raise _error('WaitForSingleObject')
            raise RuntimeError(f'Unexpected startup mutex wait result: {result:#x}')
        except BaseException as failure:
            try:
                self._close(handle)
            except BaseException as cleanup_failure:
                failure.add_note(str(cleanup_failure))
            raise

    def release(self):
        handle = self._handle
        if handle is None:
            return
        self._handle = None
        failure = None
        try:
            if not self._api.kernel32.ReleaseMutex(handle):
                failure = _error('ReleaseMutex')
        except BaseException as release_failure:
            failure = release_failure
        try:
            self._close(handle)
        except BaseException as cleanup_failure:
            if failure is None:
                failure = cleanup_failure
            else:
                failure.add_note(str(cleanup_failure))
        if failure is not None:
            raise failure
