import contextlib
import io
import json
from unittest import TestCase
from unittest.mock import Mock, patch

import server


class ServiceLifecycleTests(TestCase):
    def setUp(self):
        self.service = Mock()
        self.service.cache = {'demo': 'cached'}
        self.tray = Mock()
        self.guard = Mock()

    def run_main(self, *, serve_error=None, tray_error=None):
        self.service.serve_forever.side_effect = serve_error
        self.tray.start.side_effect = tray_error
        with patch.object(server, 'ConverterServer', return_value=self.service), \
                patch.object(server, 'WindowsTray', return_value=self.tray), \
                patch.object(server, 'StartupLock', return_value=self.guard), \
                patch.object(server.sys, 'platform', 'win32'), \
                patch.object(server, 'show_startup_error') as visible_error, \
                contextlib.redirect_stdout(io.StringIO()):
            result = server.main(['--port', '18761'])
        return result, visible_error

    def assert_stopped(self):
        self.service.server_close.assert_called_once()
        self.assertEqual(self.service.cache, {})
        self.tray.stop.assert_called_once()
        self.guard.release.assert_called_once()

    def test_web_shutdown_return_cleans_tray_port_and_cached_credentials(self):
        result, visible_error = self.run_main()
        self.assertEqual(result, 0)
        self.assert_stopped()
        visible_error.assert_not_called()

    def test_keyboard_interrupt_uses_the_same_cleanup(self):
        result, visible_error = self.run_main(serve_error=KeyboardInterrupt())
        self.assertEqual(result, 0)
        self.assert_stopped()
        visible_error.assert_not_called()

    def test_tray_start_failure_is_visible_and_never_runs_hidden_service(self):
        result, visible_error = self.run_main(tray_error=RuntimeError('Tray unavailable'))
        self.assertEqual(result, 1)
        self.service.serve_forever.assert_not_called()
        visible_error.assert_called_once()
        self.assert_stopped()

    def test_cleanup_failure_still_releases_startup_coordination(self):
        self.tray.stop.side_effect = OSError('Tray cleanup failed')
        with self.assertRaises(OSError):
            self.run_main()
        self.service.server_close.assert_called_once()
        self.guard.release.assert_called_once()

    def test_second_launch_reuses_service_without_creating_another_tray(self):
        health = json.dumps({'service': server.SERVICE_ID, 'project_id': server.PROJECT_ID,
                             'version': server.VERSION}).encode()
        opener = Mock()
        opener.open.return_value.__enter__ = Mock(return_value=io.BytesIO(health))
        opener.open.return_value.__exit__ = Mock(return_value=False)
        with patch.object(server, 'ConverterServer', side_effect=OSError('In use')), \
                patch.object(server.request, 'build_opener', return_value=opener), \
                patch.object(server, 'StartupLock', return_value=self.guard), \
                patch.object(server, 'WindowsTray') as tray_factory, \
                patch.object(server.webbrowser, 'open') as open_browser, \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(server.main(['--port', '18761', '--open']), 0)
        open_browser.assert_called_once_with('http://127.0.0.1:18761')
        tray_factory.assert_not_called()
        self.guard.release.assert_called_once()
