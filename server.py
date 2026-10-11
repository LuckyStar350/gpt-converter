"""Loopback-only OAuth refresh bridge with Chrome-compatible TLS transport."""
from __future__ import annotations

import argparse
import base64
from collections import OrderedDict
from datetime import datetime, timedelta, timezone
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import math
from pathlib import Path
import re
import secrets
import socket
import sys
import threading
import time
from urllib import error, parse, request
import webbrowser
from curl_cffi import requests as curl_requests
from windows_startup import StartupLock
from windows_tray import WindowsTray

VERSION = '1.3.1'
SERVICE_ID = 'local-gpt-converter'
CLIENT_ID = 'app_EMoamEEZ73f0CkXaXp7hrann'
TOKEN_URL = 'https://auth.openai.com/oauth/token'
ROOT = Path(sys.executable).resolve().parent if getattr(sys, 'frozen', False) else Path(__file__).resolve().parent
PROJECT_ID = hashlib.sha256(str(ROOT).lower().encode()).hexdigest()[:16]
MAX_BODY = 128 * 1024
ASSETS = {
    'index.html': 'text/html; charset=utf-8', 'styles.css': 'text/css; charset=utf-8',
    'app.js': 'text/javascript; charset=utf-8', 'converter.js': 'text/javascript; charset=utf-8',
    'archive.js': 'text/javascript; charset=utf-8', 'online.js': 'text/javascript; charset=utf-8',
    'icon.svg': 'image/svg+xml',
}


class RefreshError(Exception):
    def __init__(self, message, code='refresh_failed', status=502, upstream_status=None):
        super().__init__(message)
        self.code = code
        self.status = status
        self.upstream_status = upstream_status


def validate_options(payload):
    if not isinstance(payload, dict):
        raise RefreshError('请求必须是 JSON 对象', 'bad_request', 400)
    token = payload.get('refresh_token')
    if not isinstance(token, str) or not 16 <= len(token) <= 32768 or re.search(r'\s', token):
        raise RefreshError('refresh_token 格式不正确', 'bad_request', 400)
    client_id = payload.get('client_id') or CLIENT_ID
    if not isinstance(client_id, str) or not re.fullmatch(r'[A-Za-z0-9_.-]{1,200}', client_id):
        raise RefreshError('client_id 格式不正确', 'bad_request', 400)
    mode = payload.get('network_mode', 'auto')
    if mode not in ('auto', 'direct', 'proxy'):
        raise RefreshError('网络模式不正确', 'bad_request', 400)
    proxy_url = payload.get('proxy_url', '')
    if not isinstance(proxy_url, str) or len(proxy_url) > 2048:
        raise RefreshError('代理地址格式不正确', 'bad_request', 400)
    if mode == 'proxy':
        try:
            proxy = parse.urlsplit(proxy_url)
            valid = proxy.scheme in ('http', 'https', 'socks5', 'socks5h') and proxy.hostname and proxy.port and not proxy.query and not proxy.fragment and proxy.path in ('', '/')
        except ValueError:
            valid = False
        if not valid:
            raise RefreshError('代理地址需包含协议和端口，例如 http://127.0.0.1:7890 或 socks5h://127.0.0.1:1080', 'bad_proxy', 400)
    else:
        proxy_url = ''
    return {'refresh_token': token, 'client_id': client_id, 'network_mode': mode, 'proxy_url': proxy_url}


def oauth_error(status, body, headers=None):
    code = ''
    description = ''
    try:
        payload = json.loads(body)
        value = payload.get('error') if isinstance(payload, dict) else None
        if isinstance(value, dict):
            description = str(value.get('message') or value.get('error_description') or '').lower()
            value = value.get('code') or value.get('type')
        else:
            description = str(payload.get('error_description') or '').lower() if isinstance(payload, dict) else ''
        if isinstance(value, str) and re.fullmatch(r'[A-Za-z0-9_.-]{1,80}', value):
            code = value
    except (ValueError, TypeError):
        pass
    # Recognize legacy invalid_grant descriptions without exposing response text.
    if code == 'invalid_grant' and ('already been used' in description or 'reused' in description):
        code = 'refresh_token_reused'
    messages = {
        'invalid_grant': 'RT 已失效、已使用或不属于当前客户端，请使用最新 RT 并检查 client_id',
        'refresh_token_reused': 'RT 已被使用：请导入上次刷新保存的新 RT / CPA / Sub2 凭据；若未保存，需要重新登录获取，旧 RT 无法恢复',
        'invalid_refresh_token': 'RT 无效，请使用完整的最新 refresh_token，并检查它所属的 client_id',
        'refresh_token_expired': 'RT 已过期，请重新获取账号凭据',
        'refresh_token_invalidated': 'RT 已被撤销，请重新获取账号凭据',
        'invalid_client': 'OAuth 客户端不匹配，请在网络设置中填写该 RT 的 client_id',
        'invalid_scope': 'OAuth 服务拒绝了刷新权限',
        'unsupported_country_region_territory': 'OAuth 服务拒绝当前出口地区（HTTP 403），请在网络设置中选择可访问 OpenAI 的代理出口',
        'access_denied': 'OAuth 服务拒绝访问，请检查代理出口及账号权限',
    }
    if code in messages:
        message = messages[code]
    elif status == 429:
        message = 'OAuth 服务请求过于频繁，请稍后再试'
    elif status == 403:
        mitigated = (headers or {}).get('cf-mitigated', '')
        code = 'oauth_challenge' if mitigated == 'challenge' or body.lstrip().startswith(b'<') else 'oauth_forbidden'
        message = 'OAuth 网络验证拦截（HTTP 403）' if code == 'oauth_challenge' else 'OAuth 服务拒绝连接（HTTP 403）'
        message += '；请先点击「检测连接」，检查代理出口。更换出口后再试，连续重试同一 RT 无法解决网络拒绝'
    elif status in (400, 401):
        message = 'OAuth 服务拒绝了该 RT，请检查凭据与 client_id'
    elif 300 <= status < 400:
        message = 'OAuth 服务返回了重定向，已停止发送凭据'
    else:
        message = f'OAuth 服务返回 HTTP {status}'
    return RefreshError(message, code or 'oauth_rejected', 429 if status == 429 else 502, status)


def resolve_proxy(options):
    if options['network_mode'] == 'proxy':
        return options['proxy_url']
    if options['network_mode'] == 'direct' or request.proxy_bypass('auth.openai.com'):
        return ''
    # libcurl does not read Windows' system proxy. Resolve it explicitly, including
    # the environment override, then disable implicit proxy selection in libcurl.
    proxies = request.getproxies()
    value = proxies.get('https') or proxies.get('all') or proxies.get('http') or ''
    if value and '://' not in value:
        value = 'http://' + value
    if value:
        validate_options({**options, 'network_mode': 'proxy', 'proxy_url': value})
    return value


def connection_info(options):
    proxy = resolve_proxy(options)
    if not proxy:
        return '直接连接'
    parts = parse.urlsplit(proxy)
    # Never expose usernames, passwords, query strings or full proxy URLs.
    host = f'[{parts.hostname}]' if ':' in parts.hostname else parts.hostname
    return f'{parts.scheme}://{host}:{parts.port}'


def oauth_request(options):
    form = {'grant_type': 'refresh_token', 'refresh_token': options['refresh_token'],
            'client_id': options['client_id']}
    try:
        with curl_requests.Session(trust_env=False) as session:
            response = session.post(TOKEN_URL, data=form, proxy=resolve_proxy(options),
                                    impersonate='chrome', timeout=30, allow_redirects=False,
                                    verify=True, stream=True, headers={'Accept': 'application/json'})
            try:
                chunks = []
                size = 0
                for chunk in response.iter_content():
                    size += len(chunk)
                    if size > MAX_BODY:
                        raise RefreshError('OAuth 响应过大', 'invalid_response')
                    chunks.append(chunk)
                return response.status_code, bytes(b''.join(chunks)), dict(response.headers)
            finally:
                response.close()
    except curl_requests.exceptions.RequestException as exc:
        code = int(getattr(exc, 'code', 0) or 0)
        if code == 28:
            raise RefreshError('刷新请求超时；请求可能已到达 OAuth 服务，请勿连续重试旧 RT', 'timeout', 504) from None
        if code in (35, 60, 77):
            raise RefreshError('TLS 连接或证书验证失败，请检查系统时间及代理证书', 'tls_error') from None
        if code in (5, 7, 97):
            raise RefreshError('无法连接代理或 OAuth 服务，请确认代理程序正在运行，并检查地址、端口及代理出口', 'network_error') from None
        raise RefreshError('无法连接 OAuth 服务，请检查网络或代理，并点击「检测连接」', 'network_error') from None


def refresh_oauth(options):
    status, body, headers = oauth_request(options)
    if status != 200:
        raise oauth_error(status, body, headers)
    try:
        payload = json.loads(body)
    except (ValueError, UnicodeDecodeError):
        raise RefreshError('OAuth 返回的不是有效 JSON', 'invalid_response') from None
    if not isinstance(payload, dict) or not isinstance(payload.get('access_token'), str) or not payload['access_token'].strip():
        raise RefreshError('OAuth 响应缺少 access_token', 'invalid_response')
    for key in ('refresh_token', 'id_token'):
        if payload.get(key) is not None and not isinstance(payload[key], str):
            raise RefreshError('OAuth Token 字段类型不正确', 'invalid_response')
    now = datetime.now(timezone.utc)
    result = {
        'access_token': payload['access_token'],
        'refresh_token': payload.get('refresh_token') or options['refresh_token'],
        'id_token': payload.get('id_token') or '',
        'client_id': options['client_id'],
        'last_refresh': now.isoformat().replace('+00:00', 'Z'),
    }
    expires_in = payload.get('expires_in')
    if isinstance(expires_in, (int, float)) and not isinstance(expires_in, bool) and 0 < expires_in < 10 * 365 * 86400:
        result['expires_at'] = (now + timedelta(seconds=expires_in)).isoformat().replace('+00:00', 'Z')
    return result


def check_network(payload):
    # Probe with a fake token. This never consumes or sends a user's RT.
    if not isinstance(payload, dict):
        raise RefreshError('请求必须是 JSON 对象', 'bad_request', 400)
    options = validate_options({**payload, 'refresh_token': 'rt_FAKE_NETWORK_CHECK_NOT_A_REAL_TOKEN'})
    connection = connection_info(options)
    try:
        status, body, headers = oauth_request(options)
        mapped = oauth_error(status, body, headers)
        reachable = status in (400, 401) and mapped.code in ('invalid_grant', 'invalid_refresh_token', 'refresh_token_expired', 'refresh_token_invalidated')
        return {'reachable': reachable, 'connection': connection, 'upstream_status': status,
                'code': mapped.code, 'message': 'OAuth 服务可达，连接正常；实际刷新仍需有效 RT 和匹配的 client_id' if reachable else str(mapped)}
    except RefreshError as exc:
        return {'reachable': False, 'connection': connection, 'code': exc.code, 'message': str(exc)}


def cached_access_is_fresh(result):
    """Respect both OAuth expiry and JWT exp without treating decoding as verification."""
    expiries = []
    try:
        expiry = datetime.fromisoformat(result.get('expires_at', '').replace('Z', '+00:00'))
        if expiry.tzinfo is not None:
            expiries.append(expiry.timestamp())
    except (ValueError, TypeError, AttributeError, OverflowError):
        pass
    try:
        parts = result.get('access_token', '').split('.')
        if len(parts) == 3:
            encoded = parts[1] + '=' * (-len(parts[1]) % 4)
            claims = json.loads(base64.urlsafe_b64decode(encoded))
            expiry = claims.get('exp') if isinstance(claims, dict) else None
            if isinstance(expiry, (int, float)) and not isinstance(expiry, bool) and math.isfinite(expiry):
                expiries.append(expiry)
    except (ValueError, TypeError, AttributeError, UnicodeDecodeError, OverflowError, RecursionError):
        pass
    return not expiries or min(expiries) > time.time() + 30


class ConverterServer(ThreadingHTTPServer):
    daemon_threads = True
    # Windows SO_REUSEADDR allows several listeners on one port. Keep one owner.
    allow_reuse_address = sys.platform != 'win32'

    def __init__(self, address, refresh_fn=refresh_oauth):
        super().__init__(address, Handler)
        self.session_key = secrets.token_urlsafe(32)
        self.refresh_fn = refresh_fn
        self.cache = OrderedDict()
        self.cache_lock = threading.Lock()
        self.token_locks = [threading.Lock() for _ in range(64)]
        self.refresh_slots = threading.BoundedSemaphore(3)
        self.startup_lock = None

    def service_actions(self):
        # Release startup coordination only after the HTTP loop accepts requests.
        if self.startup_lock is not None:
            self.startup_lock.release()
            self.startup_lock = None

    def refresh(self, payload):
        options = validate_options(payload)
        # Cache successful rotations in memory; repeated requests reuse the new RT.
        key = hashlib.sha256((options['client_id'] + '\0' + options['refresh_token']).encode()).hexdigest()
        while True:
            with self.cache_lock:
                for expired in [item for item, (stamp, _, entry_lock) in self.cache.items()
                                if time.monotonic() - stamp > 600 and not entry_lock.locked()]:
                    del self.cache[expired]
                entry = self.cache.get(key)
                # Every known generation of a rotating RT shares one lock.
                lock = entry[2] if entry else self.token_locks[int(key[:8], 16) % len(self.token_locks)]
            with lock:
                previous_result = None
                with self.cache_lock:
                    entry = self.cache.get(key)
                    # An alias may have been registered while we waited for its
                    # original stripe. Release that stripe and use its shared lock.
                    if entry and entry[2] is not lock:
                        continue
                    if entry:
                        self.cache.move_to_end(key)
                        previous_result = entry[1]
                        if cached_access_is_fresh(previous_result):
                            return previous_result
                        # A short-lived AT must be renewed with the latest known RT.
                        options = {**options, 'refresh_token': previous_result['refresh_token']}
                        # Keep all aliases discoverable while their renewal is in
                        # flight, even if the original cache lifetime has elapsed.
                        stamp = time.monotonic()
                        for alias, (_, cached_result, alias_lock) in list(self.cache.items()):
                            if cached_result is previous_result:
                                self.cache[alias] = (stamp, cached_result, alias_lock)
                if not self.refresh_slots.acquire(blocking=False):
                    raise RefreshError('本机已有多个刷新请求，请稍后再试', 'busy', 429)
                try:
                    result = self.refresh_fn(options)
                finally:
                    self.refresh_slots.release()
                with self.cache_lock:
                    stamp = time.monotonic()
                    if previous_result is not None:
                        for alias, (_, cached_result, _) in list(self.cache.items()):
                            if cached_result is previous_result:
                                self.cache[alias] = (stamp, result, lock)
                    self.cache[key] = (stamp, result, lock)
                    # Cache both generations: another tab submitting the rotated RT
                    # should reuse the fresh result instead of rotating it again.
                    rotated_key = hashlib.sha256((options['client_id'] + '\0' + result['refresh_token']).encode()).hexdigest()
                    self.cache[rotated_key] = (stamp, result, lock)
                    consumed_key = hashlib.sha256((options['client_id'] + '\0' + options['refresh_token']).encode()).hexdigest()
                    while len(self.cache) > 200:
                        removable = next((alias for alias, (_, _, alias_lock) in self.cache.items()
                                          if not alias_lock.locked() or
                                          (alias_lock is lock and alias not in (key, consumed_key, rotated_key))), None)
                        if removable is None:
                            break  # Temporarily keep aliases of other in-flight renewals.
                        del self.cache[removable]
                return result


class Handler(BaseHTTPRequestHandler):
    server_version = 'LocalGPTConverter'

    def log_message(self, format, *args):
        pass  # No request body, Token, proxy password or OAuth response logs.

    def valid_host(self):
        port = self.server.server_port
        return self.headers.get('Host') in (f'127.0.0.1:{port}', f'localhost:{port}')

    def local_origin(self):
        origin = self.headers.get('Origin')
        return origin is None or origin in (f'http://127.0.0.1:{self.server.server_port}', f'http://localhost:{self.server.server_port}')

    def send_data(self, status, body, content_type='application/json; charset=utf-8'):
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('X-Frame-Options', 'DENY')
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def send_json(self, status, value):
        self.send_data(status, json.dumps(value, ensure_ascii=False).encode('utf-8'))

    def do_GET(self):
        if not self.valid_host() or not self.local_origin():
            self.send_json(403, {'error': '只接受本机同源请求'})
            return
        path = parse.urlsplit(self.path).path
        if path == '/api/health':
            try:
                connection = connection_info(validate_options({'refresh_token': 'rt_FAKE_HEALTH_NOT_A_REAL_TOKEN'}))
            except RefreshError:
                connection = '系统代理配置无效，请选择自定义代理'
            self.send_json(200, {'service': SERVICE_ID, 'version': VERSION, 'project_id': PROJECT_ID,
                                 'session_key': self.server.session_key, 'default_client_id': CLIENT_ID,
                                 'connection': connection})
            return
        filename = 'index.html' if path == '/' else path.lstrip('/')
        if filename not in ASSETS:
            self.send_json(404, {'error': '文件不存在'})
            return
        try:
            body = (ROOT / filename).read_bytes()
        except OSError:
            self.send_json(404, {'error': '工具文件缺失，请完整解压程序目录'})
            return
        self.send_data(200, body, ASSETS[filename])

    def do_POST(self):
        if not self.valid_host() or not self.local_origin() or not secrets.compare_digest(self.headers.get('X-Local-GPT-Key', ''), self.server.session_key):
            self.send_json(403, {'error': '本机请求校验失败，请刷新页面后重试'})
            return
        path = parse.urlsplit(self.path).path
        if path == '/api/shutdown':
            self.send_json(200, {'success': True})
            threading.Thread(target=self.server.shutdown, daemon=True).start()
            return
        if path not in ('/api/refresh', '/api/network-check'):
            self.send_json(404, {'error': '接口不存在'})
            return
        if self.headers.get_content_type() != 'application/json' or self.headers.get('Transfer-Encoding'):
            self.send_json(400, {'error': '需要 application/json 请求'})
            return
        try:
            length = int(self.headers.get('Content-Length', '0'))
            if not 0 < length <= MAX_BODY:
                self.send_json(413, {'error': '请求内容为空或过大'})
                return
            self.connection.settimeout(10)
            payload = json.loads(self.rfile.read(length))
            if path == '/api/network-check':
                self.send_json(200, check_network(payload))
                return
            result = self.server.refresh(payload)
            self.send_json(200, {'success': True, 'tokens': result})
        except (ValueError, UnicodeDecodeError):
            self.send_json(400, {'error': 'JSON 请求格式不正确'})
        except RefreshError as exc:
            self.send_json(exc.status, {'error': str(exc), 'code': exc.code, 'upstream_status': exc.upstream_status})
        except (TimeoutError, socket.timeout):
            self.send_json(408, {'error': '读取请求超时'})
        except Exception:
            self.send_json(500, {'error': '本机刷新服务处理失败'})


def show_startup_error(message):
    print(message, file=sys.stderr, flush=True)
    if sys.platform == 'win32':
        import ctypes
        from ctypes import wintypes
        user32 = ctypes.WinDLL('user32', use_last_error=True)
        user32.MessageBoxW.argtypes = [wintypes.HWND, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.UINT]
        user32.MessageBoxW.restype = ctypes.c_int
        user32.MessageBoxW(None, message, 'GPT 转换工具', 0x10)


def main(argv=None):
    parser = argparse.ArgumentParser(description='Local GPT converter and OAuth refresh bridge')
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--open', action='store_true', help='Open the local page in the default browser')
    parser.add_argument('--no-tray', action='store_true', help='Disable the Windows tray for headless diagnostics')
    args = parser.parse_args(argv)
    if not 1 <= args.port <= 65515:
        parser.error('port must be between 1 and 65515')
    server = None
    tray = None
    startup = StartupLock(f'Local\\GPTConverter-{PROJECT_ID}')
    try:
        startup.acquire()
        for port in range(args.port, args.port + 20):
            url = f'http://127.0.0.1:{port}'
            try:
                server = ConverterServer(('127.0.0.1', port))
                break
            except OSError:
                try:
                    opener = request.build_opener(request.ProxyHandler({}))
                    with opener.open(url + '/api/health', timeout=1) as response:
                        health = json.load(response)
                    if health.get('service') == SERVICE_ID and health.get('project_id') == PROJECT_ID and health.get('version') == VERSION:
                        if args.open:
                            webbrowser.open(url)
                        print(f'Already running: {url}', flush=True)
                        return 0
                except (ValueError, OSError, error.URLError):
                    pass
        if server is None:
            raise RuntimeError('没有可用的本机端口，请关闭旧服务后重试。')
        server.startup_lock = startup
        if sys.platform == 'win32' and not args.no_tray:
            tray = WindowsTray(lambda: webbrowser.open(url), server.shutdown, icon_path=ROOT / 'icon.ico')
            tray.start()
        print(f'GPT converter: {url} (local only)', flush=True)
        if args.open:
            webbrowser.open(url)
        server.serve_forever(poll_interval=0.2)
    except KeyboardInterrupt:
        pass
    except (OSError, RuntimeError) as exc:
        show_startup_error(f'无法启动 GPT 转换工具：{exc}\n工具未继续在后台运行，请修复后重新启动。')
        return 1
    finally:
        try:
            if server is not None:
                server.server_close()
                server.cache.clear()
        finally:
            try:
                if tray is not None:
                    tray.stop()
            finally:
                startup.release()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
