import concurrent.futures
import base64
import hashlib
import io
import json
from pathlib import Path
import sys
import threading
import time
import unittest
from unittest.mock import patch
from urllib import error, request, parse

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import server

RT = 'rt_DEMO_ONLY_not_a_real_token_001'
NEW_RT = 'rt_DEMO_ONLY_rotated_token_001'


class Response:
    def __init__(self, value):
        self.value = json.dumps(value).encode()

    def read(self, size):
        return self.value[:size]

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class ServerTests(unittest.TestCase):
    def setUp(self):
        self.calls = []

        def refresh(options):
            self.calls.append(options)
            return {'access_token': 'DEMO_access_token', 'refresh_token': NEW_RT, 'id_token': '', 'last_refresh': '2026-10-10T00:00:00Z'}

        self.service = server.ConverterServer(('127.0.0.1', 0), refresh)
        self.thread = threading.Thread(target=self.service.serve_forever, daemon=True)
        self.thread.start()
        self.base = f'http://127.0.0.1:{self.service.server_port}'
        self.opener = request.build_opener(request.ProxyHandler({}))

    def tearDown(self):
        self.service.shutdown()
        self.service.server_close()
        self.thread.join(timeout=2)

    def get(self, path, headers=None):
        return self.opener.open(request.Request(self.base + path, headers=headers or {}), timeout=3)

    def post(self, payload, headers=None, path='/api/refresh'):
        values = {'Content-Type': 'application/json', 'Origin': self.base, 'X-Local-GPT-Key': self.service.session_key}
        if headers:
            values.update(headers)
        return self.opener.open(request.Request(self.base + path, data=json.dumps(payload).encode(), headers=values, method='POST'), timeout=3)

    def test_health_and_static_assets(self):
        with self.get('/api/health') as response:
            health = json.load(response)
            self.assertEqual(health['service'], server.SERVICE_ID)
            self.assertEqual(health['session_key'], self.service.session_key)
            self.assertEqual(response.headers['Cache-Control'], 'no-store')
        with self.get('/') as response:
            self.assertIn(b'connect-src', response.read())
        with self.get('/online.js') as response:
            self.assertIn(b'GPTOnline', response.read())

    def test_private_files_and_traversal_are_not_served(self):
        for path in ('/server.py', '/README.md', '/../server.py', '/examples/cpa/demo-001.json'):
            with self.assertRaises(error.HTTPError) as caught:
                self.get(path)
            self.assertEqual(caught.exception.code, 404)

    def test_invalid_host_origin_and_session_are_rejected(self):
        for headers in ({'Host': f'evil.example:{self.service.server_port}'}, {'Origin': 'https://evil.example'}, {'X-Local-GPT-Key': ''}, {'Origin': 'null'}):
            with self.assertRaises(error.HTTPError) as caught:
                self.post({'refresh_token': RT}, headers)
            self.assertEqual(caught.exception.code, 403)
        with self.assertRaises(error.HTTPError):
            self.get('/api/health', {'Origin': 'https://evil.example'})
        self.assertEqual(self.calls, [])

    def test_refresh_rotation_and_successful_request_cache(self):
        for _ in range(2):
            with self.post({'refresh_token': RT}) as response:
                self.assertEqual(json.load(response)['tokens']['refresh_token'], NEW_RT)
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.calls[0]['client_id'], server.CLIENT_ID)

    def test_concurrent_refresh_of_same_rt_uses_one_upstream_request(self):
        original = self.service.refresh_fn

        def slow(options):
            time.sleep(0.03)
            return original(options)

        self.service.refresh_fn = slow
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
            results = list(executor.map(lambda _: self.service.refresh({'refresh_token': RT}), range(4)))
        self.assertEqual(len(results), 4)
        self.assertEqual(len(self.calls), 1)

    def test_invalid_fields_and_proxy_are_rejected(self):
        for payload in ({'refresh_token': []}, {'refresh_token': 'short'}, {'refresh_token': RT, 'client_id': 'bad client'}, {'refresh_token': RT, 'network_mode': 'proxy', 'proxy_url': 'file:///secret'}):
            with self.assertRaises(error.HTTPError) as caught:
                self.post(payload)
            self.assertEqual(caught.exception.code, 400)
        self.assertEqual(self.calls, [])

    def test_rotated_token_and_original_token_share_fresh_cache(self):
        first = self.service.refresh({'refresh_token': RT})
        self.assertEqual(self.service.refresh({'refresh_token': NEW_RT}), first)
        self.assertEqual(self.service.refresh({'refresh_token': RT}), first)
        self.assertEqual(len(self.calls), 1)

    def test_expiring_access_uses_latest_rt_once_across_cached_generations(self):
        def refresh(options):
            self.calls.append(options.copy())
            time.sleep(0.03)
            expiry = time.time() + (10 if len(self.calls) == 1 else 3600)
            claims = base64.urlsafe_b64encode(json.dumps({'exp': expiry}).encode()).decode().rstrip('=')
            return {'access_token': f'DEMO.{claims}.SIGNATURE',
                    'refresh_token': NEW_RT if len(self.calls) == 1 else NEW_RT + '_next',
                    'expires_at': '2030-01-01T00:00:00Z'}

        self.service.refresh_fn = refresh
        self.service.refresh({'refresh_token': RT})
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
            results = list(executor.map(lambda token: self.service.refresh({'refresh_token': token}), [RT, NEW_RT, RT, NEW_RT]))
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(self.calls[1]['refresh_token'], NEW_RT)
        self.assertTrue(all(result['refresh_token'] == NEW_RT + '_next' for result in results))
        self.assertEqual(self.service.refresh({'refresh_token': RT}), results[0])
        self.assertEqual(len(self.calls), 2)

    def test_oauth_expiry_prevents_reusing_cached_opaque_access_token(self):
        self.service.refresh_fn = lambda options: {'access_token': 'DEMO_opaque_access',
            'refresh_token': NEW_RT, 'expires_at': '2000-01-01T00:00:00Z'}
        self.service.refresh({'refresh_token': RT})
        self.service.refresh_fn = lambda options: (self.calls.append(options.copy()) or {
            'access_token': 'DEMO_fresh_access', 'refresh_token': NEW_RT + '_next',
            'expires_at': '2030-01-01T00:00:00Z'})
        result = self.service.refresh({'refresh_token': RT})
        self.assertEqual(self.calls[0]['refresh_token'], NEW_RT)
        self.assertEqual(result['refresh_token'], NEW_RT + '_next')

    def test_alias_registered_while_waiting_switches_to_shared_lock(self):
        selected = threading.Event()
        routed = threading.Event()
        renewing = threading.Event()
        duplicate = threading.Event()
        finish = threading.Event()
        calls_lock = threading.Lock()

        class WaitingStripe:
            def __init__(self):
                self.inner = threading.Lock()

            def __enter__(self):
                selected.set()
                self.inner.acquire()
                return self

            def __exit__(self, *args):
                self.inner.release()
                routed.set()

            def locked(self):
                return self.inner.locked()

        def stripe(token):
            key = hashlib.sha256((server.CLIENT_ID + '\0' + token).encode()).hexdigest()
            return int(key[:8], 16) % len(self.service.token_locks)

        self.assertNotEqual(stripe(RT), stripe(NEW_RT))
        waiting_stripe = WaitingStripe()
        waiting_stripe.inner.acquire()
        self.service.token_locks[stripe(NEW_RT)] = waiting_stripe

        def refresh(options):
            with calls_lock:
                self.calls.append(options.copy())
                count = len(self.calls)
            if count == 2:
                renewing.set()
                finish.wait(3)
            elif count > 2:
                duplicate.set()
                routed.set()
                finish.wait(3)
            expiry = time.time() + (10 if count == 1 else 3600)
            claims = base64.urlsafe_b64encode(json.dumps({'exp': expiry}).encode()).decode().rstrip('=')
            return {'access_token': f'DEMO.{claims}.SIGNATURE',
                    'refresh_token': NEW_RT if count == 1 else NEW_RT + '_next'}

        self.service.refresh_fn = refresh
        released = False
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            try:
                waiting = executor.submit(self.service.refresh, {'refresh_token': NEW_RT})
                self.assertTrue(selected.wait(2))
                self.service.refresh({'refresh_token': RT})
                renewal = executor.submit(self.service.refresh, {'refresh_token': RT})
                self.assertTrue(renewing.wait(2))
                waiting_stripe.inner.release()
                released = True
                self.assertTrue(routed.wait(2))
                self.assertFalse(duplicate.is_set())
            finally:
                if not released:
                    waiting_stripe.inner.release()
                finish.set()
            self.assertEqual(waiting.result(timeout=2), renewal.result(timeout=2))
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(self.calls[1]['refresh_token'], NEW_RT)

    def test_ttl_cleanup_preserves_aliases_during_inflight_refresh(self):
        clock = [500.0]
        renewing = threading.Event()
        routed = threading.Event()
        duplicate = threading.Event()
        finish = threading.Event()
        calls_lock = threading.Lock()

        class ObservedLock:
            def __init__(self):
                self.inner = threading.Lock()
                self.attempts = 0

            def __enter__(self):
                self.attempts += 1
                if self.attempts == 3:
                    routed.set()
                self.inner.acquire()
                return self

            def __exit__(self, *args):
                self.inner.release()

            def locked(self):
                return self.inner.locked()

        key = hashlib.sha256((server.CLIENT_ID + '\0' + RT).encode()).hexdigest()
        shared = ObservedLock()
        self.service.token_locks[int(key[:8], 16) % len(self.service.token_locks)] = shared

        def refresh(options):
            with calls_lock:
                self.calls.append(options.copy())
                count = len(self.calls)
            if count == 2:
                renewing.set()
                finish.wait(3)
            elif count > 2:
                duplicate.set()
                routed.set()
                finish.wait(3)
            expiry = time.time() + (10 if count == 1 else 3600)
            claims = base64.urlsafe_b64encode(json.dumps({'exp': expiry}).encode()).decode().rstrip('=')
            return {'access_token': f'DEMO.{claims}.SIGNATURE',
                    'refresh_token': NEW_RT if count == 1 else NEW_RT + '_next'}

        self.service.refresh_fn = refresh
        with patch.object(server.time, 'monotonic', side_effect=lambda: clock[0]):
            self.service.refresh({'refresh_token': RT})
            clock[0] = 1099.0
            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
                try:
                    renewal = executor.submit(self.service.refresh, {'refresh_token': RT})
                    self.assertTrue(renewing.wait(2))
                    clock[0] = 1101.0
                    latest = executor.submit(self.service.refresh, {'refresh_token': NEW_RT})
                    self.assertTrue(routed.wait(2))
                    self.assertFalse(duplicate.is_set())
                finally:
                    finish.set()
                self.assertEqual(renewal.result(timeout=2), latest.result(timeout=2))
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(self.calls[1]['refresh_token'], NEW_RT)

    def test_capacity_cleanup_preserves_aliases_during_inflight_refresh(self):
        renewing = threading.Event()
        routed = threading.Event()
        duplicate = threading.Event()
        finish = threading.Event()
        calls_lock = threading.Lock()

        class ObservedLock:
            def __init__(self):
                self.inner = threading.Lock()
                self.attempts = 0

            def __enter__(self):
                self.attempts += 1
                if self.attempts == 3:
                    routed.set()
                self.inner.acquire()
                return self

            def __exit__(self, *args):
                self.inner.release()

            def locked(self):
                return self.inner.locked()

        def stripe(token):
            key = hashlib.sha256((server.CLIENT_ID + '\0' + token).encode()).hexdigest()
            return int(key[:8], 16) % len(self.service.token_locks)

        shared_stripe = stripe(RT)
        self.service.token_locks[shared_stripe] = ObservedLock()

        def refresh(options):
            token = options['refresh_token']
            if token not in (RT, NEW_RT):
                return {'access_token': 'DEMO_filler_access', 'refresh_token': token + '_rotated'}
            with calls_lock:
                self.calls.append(options.copy())
                count = len(self.calls)
            if count == 2:
                renewing.set()
                finish.wait(3)
            elif count > 2:
                duplicate.set()
                routed.set()
                finish.wait(3)
            expiry = time.time() + (10 if count == 1 else 3600)
            claims = base64.urlsafe_b64encode(json.dumps({'exp': expiry}).encode()).decode().rstrip('=')
            return {'access_token': f'DEMO.{claims}.SIGNATURE',
                    'refresh_token': NEW_RT if count == 1 else NEW_RT + '_next'}

        self.service.refresh_fn = refresh
        self.service.refresh({'refresh_token': RT})
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            try:
                renewal = executor.submit(self.service.refresh, {'refresh_token': RT})
                self.assertTrue(renewing.wait(2))
                completed = 0
                candidate = 0
                while completed < 100:
                    filler = f'rt_DEMO_CAPACITY_filler_{candidate:04d}'
                    candidate += 1
                    if stripe(filler) == shared_stripe:
                        continue
                    self.service.refresh({'refresh_token': filler})
                    completed += 1
                latest = executor.submit(self.service.refresh, {'refresh_token': NEW_RT})
                self.assertTrue(routed.wait(2))
                self.assertFalse(duplicate.is_set())
            finally:
                finish.set()
            self.assertEqual(renewal.result(timeout=2), latest.result(timeout=2))
        self.assertEqual(len(self.calls), 2)

    def test_network_check_uses_fake_token_and_never_refreshes_input(self):
        body = json.dumps({'error': 'invalid_refresh_token'}).encode()
        with patch.object(server, 'oauth_request', return_value=(401, body, {})) as outgoing:
            with self.post({'refresh_token': RT}, path='/api/network-check') as response:
                result = json.load(response)
        self.assertTrue(result['reachable'])
        self.assertNotEqual(outgoing.call_args.args[0]['refresh_token'], RT)
        self.assertEqual(self.calls, [])

    def test_upstream_failure_does_not_echo_tokens_or_proxy_passwords(self):
        mapped = server.oauth_error(400, json.dumps({'error': 'invalid_grant', 'error_description': RT + ' secret-password'}).encode())
        self.assertNotIn(RT, str(mapped))
        self.assertNotIn('secret-password', str(mapped))
        self.assertEqual(mapped.code, 'invalid_grant')
        self.service.refresh_fn = lambda _: (_ for _ in ()).throw(mapped)
        with self.assertRaises(error.HTTPError) as caught:
            self.post({'refresh_token': RT})
        self.assertNotIn(RT, caught.exception.read().decode())

    def test_shutdown_endpoint(self):
        with self.post({}, path='/api/shutdown') as response:
            self.assertTrue(json.load(response)['success'])
        self.thread.join(timeout=2)
        self.assertFalse(self.thread.is_alive())


class OAuthTests(unittest.TestCase):
    def options(self, **values):
        return server.validate_options({'refresh_token': RT, **values})

    def test_invalid_jwt_expiry_never_crashes_or_hides_oauth_expiry(self):
        for claims in (json.dumps({'exp': 10 ** 400}), '[' * 1200 + '0' + ']' * 1200):
            with self.subTest(claims=claims[:40]):
                encoded = base64.urlsafe_b64encode(claims.encode()).decode().rstrip('=')
                result = {'access_token': f'DEMO.{encoded}.SIGNATURE'}
                self.assertTrue(server.cached_access_is_fresh(result))
                result['expires_at'] = '2000-01-01T00:00:00Z'
                self.assertFalse(server.cached_access_is_fresh(result))

    def test_fixed_endpoint_form_rotation_and_expiry(self):
        class CurlResponse:
            status_code = 200
            headers = {'content-type': 'application/json'}
            def iter_content(self):
                yield json.dumps({'access_token': 'DEMO-access', 'refresh_token': NEW_RT, 'expires_in': 3600}).encode()
            def close(self):
                pass
        with patch.object(server.curl_requests, 'Session') as factory:
            session = factory.return_value.__enter__.return_value
            session.post.return_value = CurlResponse()
            result = server.refresh_oauth(self.options(network_mode='proxy', proxy_url='socks5h://127.0.0.1:1080'))
        args, kwargs = session.post.call_args
        self.assertEqual(args[0], server.TOKEN_URL)
        self.assertEqual(kwargs['data']['grant_type'], 'refresh_token')
        self.assertEqual(set(kwargs['data']), {'grant_type', 'refresh_token', 'client_id'})
        self.assertEqual(kwargs['data']['client_id'], server.CLIENT_ID)
        self.assertEqual(kwargs['proxy'], 'socks5h://127.0.0.1:1080')
        self.assertEqual(kwargs['impersonate'], 'chrome')
        self.assertFalse(kwargs['allow_redirects'])
        self.assertTrue(kwargs['verify'])
        self.assertEqual(result['refresh_token'], NEW_RT)
        self.assertIn('expires_at', result)

    def test_absent_rotated_rt_preserves_previous_rt(self):
        with patch.object(server, 'oauth_request', return_value=(200, b'{"access_token":"DEMO-access"}', {})):
            result = server.refresh_oauth(self.options())
        self.assertEqual(result['refresh_token'], RT)
        self.assertNotIn('expires_at', result)

    def test_invalid_response_and_rejection_are_sanitized(self):
        for payload in ({'id_token': 'no-access'}, {'access_token': 'DEMO', 'refresh_token': []}):
            with patch.object(server, 'oauth_request', return_value=(200, json.dumps(payload).encode(), {})):
                with self.assertRaises(server.RefreshError):
                    server.refresh_oauth(self.options())
        body = json.dumps({'error': 'invalid_grant', 'error_description': RT}).encode()
        with patch.object(server, 'oauth_request', return_value=(400, body, {})):
            with self.assertRaises(server.RefreshError) as caught:
                server.refresh_oauth(self.options())
        self.assertNotIn(RT, str(caught.exception))

    def test_windows_and_environment_proxy_resolved_without_exposing_credentials(self):
        with patch.object(server.request, 'getproxies', return_value={'https': 'http://user:secret@127.0.0.1:7890'}), patch.object(server.request, 'proxy_bypass', return_value=False):
            self.assertEqual(server.resolve_proxy(self.options()), 'http://user:secret@127.0.0.1:7890')
            self.assertEqual(server.connection_info(self.options()), 'http://127.0.0.1:7890')
            self.assertEqual(server.resolve_proxy(self.options(network_mode='direct')), '')

    def test_403_challenge_region_and_reused_rt_have_distinct_diagnostics(self):
        challenge = server.oauth_error(403, b'<html>challenge</html>', {'cf-mitigated': 'challenge'})
        self.assertEqual(challenge.code, 'oauth_challenge')
        self.assertEqual(challenge.upstream_status, 403)
        region = server.oauth_error(403, b'{"error":{"code":"unsupported_country_region_territory"}}')
        self.assertEqual(region.code, 'unsupported_country_region_territory')
        reused = server.oauth_error(400, b'{"error":"invalid_grant","error_description":"refresh token has already been used SECRET"}')
        self.assertEqual(reused.code, 'refresh_token_reused')
        self.assertNotIn('SECRET', str(reused))

    def test_probe_distinguishes_403_from_expected_fake_token_rejection(self):
        with patch.object(server, 'oauth_request', return_value=(403, b'<html>blocked</html>', {})):
            self.assertFalse(server.check_network({})['reachable'])
        with patch.object(server, 'oauth_request', return_value=(401, b'{"error":"invalid_refresh_token"}', {})):
            result = server.check_network({'refresh_token': RT})
            self.assertTrue(result['reachable'])
            self.assertEqual(result['upstream_status'], 401)

    def test_transport_errors_do_not_expose_tokens_or_proxy_credentials(self):
        for code, expected in ((28, 'timeout'), (60, 'tls_error'), (7, 'network_error')):
            with patch.object(server.curl_requests, 'Session') as factory:
                factory.return_value.__enter__.return_value.post.side_effect = server.curl_requests.exceptions.RequestException(RT + ' password', code=code)
                with self.assertRaises(server.RefreshError) as caught:
                    server.refresh_oauth(self.options())
            self.assertEqual(caught.exception.code, expected)
            self.assertNotIn(RT, str(caught.exception))
            self.assertNotIn('password', str(caught.exception))


if __name__ == '__main__':
    unittest.main()
