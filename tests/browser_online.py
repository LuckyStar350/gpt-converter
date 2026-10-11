"""Full browser/local-server integration with mock OAuth only; no live account data."""
import base64
import json
from pathlib import Path
import sys
import threading
import time
import zipfile
from unittest.mock import patch

from playwright.sync_api import sync_playwright, expect

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import server

ARTIFACTS = ROOT / '.test-artifacts'
ARTIFACTS.mkdir(exist_ok=True)
RT1 = 'rt_DEMO_ONLY_not_a_real_token_001'
RT2 = 'rt_DEMO_ONLY_not_a_real_token_002'
calls = []
slow = False


def jwt(claims):
    encoded = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip('=')
    return f'eyJhbGciOiJub25lIn0.{encoded}.DEMO_SIGNATURE'


def mock_oauth(options):
    calls.append(options.copy())
    if slow:
        time.sleep(0.3)
    if 'bad_token' in options['refresh_token']:
        raise server.RefreshError('RT 已失效，请使用最新 RT', 'invalid_grant')
    if 'forbidden' in options['refresh_token']:
        raise server.oauth_error(403, b'<html>blocked</html>', {'cf-mitigated': 'challenge'})
    if 'reused' in options['refresh_token']:
        raise server.oauth_error(400, b'{"error":"refresh_token_reused"}')
    suffix = options['refresh_token'][-3:]
    return {
        'refresh_token': 'rt_DEMO_ONLY_rotated_token_' + suffix,
        'access_token': jwt({'exp': 1893456000, 'client_id': options['client_id'], 'https://api.openai.com/auth': {'chatgpt_account_id': 'fresh-account-' + suffix, 'chatgpt_plan_type': 'plus'}, 'https://api.openai.com/profile': {'email': f'fresh{suffix}@example.com'}}),
        'id_token': jwt({'email': f'fresh{suffix}@example.com'}),
        'client_id': options['client_id'], 'last_refresh': '2026-10-10T00:00:00Z',
        'expires_at': '2030-01-01T00:00:00Z',
    }


def main():
    global slow
    service = server.ConverterServer(('127.0.0.1', 0), mock_oauth)
    thread = threading.Thread(target=service.serve_forever, daemon=True)
    thread.start()
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel='chrome', headless=True)
            context = browser.new_context(viewport={'width': 1440, 'height': 1080}, accept_downloads=True)
            page = context.new_page()
            errors = []
            requests = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.on('request', lambda request: requests.append(request.url))
            page.goto(f'http://127.0.0.1:{service.server_port}')
            expect(page.locator('#serviceBadge')).to_have_text('本机服务已连接')
            assert not calls
            print('PASS: startup makes no upstream request')

            # Network diagnostics never use the pasted user's input.
            page.locator('#inputText').fill(RT1)
            with patch.object(server, 'oauth_request', return_value=(401, b'{"error":"invalid_refresh_token"}', {})) as diagnostic:
                page.locator('#checkNetworkButton').click()
                expect(page.locator('#networkResult')).to_contain_text('OAuth 服务可达')
                assert diagnostic.call_args.args[0]['refresh_token'] != RT1
            assert not calls
            print('PASS: network check uses a fake RT and recognizes normal OAuth rejection')

            page.locator('[data-example="rt"]').click()
            page.locator('#inputText').focus()
            page.keyboard.press('Control+Enter')
            expect(page.locator('#statusMessage')).to_contain_text('联网转换完成')
            assert len(calls) == 2
            assert '2 个账号' in page.locator('#outputCount').inner_text()
            page.locator('#revealButton').click()
            sub = json.loads(page.locator('#outputText').input_value())
            assert sub['accounts'][0]['credentials']['email'] == 'fresh001@example.com'
            assert sub['accounts'][0]['credentials']['chatgpt_account_id'] == 'fresh-account-001'
            assert sub['accounts'][0]['credentials']['refresh_token'] == 'rt_DEMO_ONLY_rotated_token_001'
            assert page.locator('#inputText').input_value() == ''
            print('PASS: RT refresh populates identity and source holds rotated credentials')

            page.locator('[data-target="rt"]').click()
            assert 'rt_DEMO_ONLY_rotated_token_001' in page.locator('#outputText').input_value()
            assert RT1 not in page.locator('#outputText').input_value()
            page.locator('#convertButton').click()
            page.locator('#deduplicate').uncheck()
            assert len(calls) == 2
            page.locator('[data-target="cpa"]').click()
            with page.expect_download() as download_info:
                page.locator('#downloadButton').click()
            path = ARTIFACTS / 'online-cpa.zip'
            download_info.value.save_as(str(path))
            with zipfile.ZipFile(path) as bundle:
                payload = json.loads(bundle.read(bundle.namelist()[0]))
                assert payload['refresh_token'] == 'rt_DEMO_ONLY_rotated_token_001'
                assert payload['account_id'] == 'fresh-account-001'
            page.locator('#revealButton').click()
            page.locator('#detailsButton').click()
            page.locator('#toast').evaluate('(element) => { element.hidden = true; }')
            page.screenshot(path=str(ARTIFACTS / 'online-result.png'), full_page=True)
            print('PASS: switching formats, repeated conversion and actual CPA download reuse fresh credentials')

            # A Codex auth.json refresh must update nested credentials and fill
            # identity from the account_id / organization aliases used by Cockpit.
            page.locator('#clearButton').click()
            nested_source = {'tokens': {'refresh_token': 'rt_DEMO_ONLY_nested_token_888',
                'access_token': jwt({'exp': 946684800, 'https://api.openai.com/auth': {'chatgpt_account_id': 'old-account'}}),
                'id_token': jwt({'email': 'old@example.com'}), 'expires_at': 946684800},
                'expires_at': 946684800, 'last_refresh': '2000-01-01T00:00:00Z'}
            original_refresh = service.refresh_fn

            def nested_oauth(options):
                return {**mock_oauth(options),
                    'access_token': jwt({'exp': 1893456000, 'https://api.openai.com/auth': {'chatgpt_org_id': 'fresh-org-888'}}),
                    'id_token': jwt({'email': 'fresh888@example.com', 'https://api.openai.com/auth': {'account_id': 'fresh-account-888'}})}

            service.refresh_fn = nested_oauth
            try:
                page.locator('#inputText').fill(json.dumps(nested_source))
                page.locator('#convertButton').click()
                expect(page.locator('#statusMessage')).to_contain_text('1 个账号刷新成功')
                if '••••' in page.locator('#outputText').input_value():
                    page.locator('#revealButton').click()
                fresh_cpa = page.locator('#outputText').input_value()
                nested_result = json.loads(fresh_cpa)
                assert nested_result['account_id'] == 'fresh-account-888'
                assert nested_result['organization_id'] == 'fresh-org-888'
                assert nested_result['tokens']['refresh_token'] == nested_result['refresh_token'] == 'rt_DEMO_ONLY_rotated_token_888'
                assert nested_result['tokens']['access_token'] == nested_result['access_token']
                assert nested_result['expired'].startswith('2030-01-01')
                page.locator('#clearButton').click()
                page.locator('#inputText').fill(fresh_cpa)
                page.locator('[data-target="sub2"]').click()
                before = len(calls)
                page.locator('#convertButton').click()
                expect(page.locator('#statusMessage')).to_contain_text('1 个账号刷新成功')
                if '••••' in page.locator('#outputText').input_value():
                    page.locator('#revealButton').click()
                fresh_sub = json.loads(page.locator('#outputText').input_value())['accounts'][0]
                assert fresh_sub['credentials']['refresh_token'] == 'rt_DEMO_ONLY_rotated_token_888'
                assert fresh_sub['credentials']['chatgpt_account_id'] == 'fresh-account-888'
                assert len(calls) == before
            finally:
                service.refresh_fn = original_refresh
            print('PASS: nested auth.json rotation, JWT aliases, reimport and Sub2 conversion keep latest credentials')

            # Client ID / proxy settings are sent only upon clicking refresh.
            page.locator('#clearButton').click()
            page.locator('#inputText').fill(RT1)
            page.locator('#onlineSettings summary').click()
            page.locator('#networkMode').select_option('proxy')
            page.locator('#httpProxyUrl').fill('http://127.0.0.1:7890')
            page.locator('#oauthClientId').fill('custom-demo-client')
            page.locator('#convertButton').click()
            expect(page.locator('#statusMessage')).to_contain_text('联网转换完成')
            assert calls[-1]['client_id'] == 'custom-demo-client'
            assert calls[-1]['proxy_url'] == 'http://127.0.0.1:7890'
            print('PASS: proxy and client ID settings reach the local refresh client')

            # Successful accounts remain downloadable when another RT is rejected.
            page.locator('#clearButton').click()
            page.locator('#inputText').fill(RT2 + '\nrt_DEMO_ONLY_bad_token_999')
            page.locator('#convertButton').click()
            expect(page.locator('#statusMessage')).to_contain_text('1 个失败')
            assert '联网获取失败' in page.locator('#reportDetails').inner_text()
            assert page.locator('#downloadButton').is_enabled()
            assert '1 个账号' in page.locator('#outputCount').inner_text()
            page.locator('[data-target="rt"]').click()
            page.locator('#revealButton').click()
            rt_output = page.locator('#outputText').input_value()
            assert 'rt_DEMO_ONLY_rotated_token_002' in rt_output
            assert 'rt_DEMO_ONLY_bad_token_999' in rt_output
            print('PASS: partial rejection preserves failed account and successful rotation')

            # A failed RT-only credential must never become a placeholder CPA file.
            for value, reason in [('rt_DEMO_ONLY_forbidden_token', 'HTTP 403'), ('rt_DEMO_ONLY_reused_token', '旧 RT 无法恢复')]:
                page.locator('#clearButton').click()
                page.locator('[data-target="cpa"]').click()
                page.locator('#inputText').fill(value)
                page.locator('#convertButton').click()
                expect(page.locator('#statusMessage')).to_contain_text('1 个失败')
                expect(page.locator('#issueList')).to_contain_text(reason)
                assert page.locator('#downloadButton').is_disabled()
                assert '0 个账号' in page.locator('#outputCount').inner_text()
            print('PASS: 403/reused-RT diagnostics, failure never generates placeholder credentials')

            page.locator('#clearButton').click()
            page.locator('#inputText').fill('rt_DEMO_ONLY_forbidden_retry')
            page.locator('#convertButton').click()
            expect(page.locator('#issueList')).to_contain_text('HTTP 403')
            original_refresh = service.refresh_fn
            service.refresh_fn = lambda options: mock_oauth({**options, 'refresh_token': 'rt_DEMO_ONLY_recovery_token_777'})
            try:
                page.locator('#convertButton').click()
                expect(page.locator('#statusMessage')).to_contain_text('1 个账号刷新成功')
                assert 'HTTP 403' not in page.locator('#issueList').inner_text()
                assert page.locator('#downloadButton').is_enabled()
            finally:
                service.refresh_fn = original_refresh
            print('PASS: retry after fixing a connection clears stale failure diagnostics')

            # Stopping never aborts an in-flight rotation.
            slow = True
            page.locator('#clearButton').click()
            page.locator('#inputText').fill('\n'.join(f'rt_DEMO_ONLY_stop_token_{i:03}' for i in range(5)))
            before = len(calls)
            page.locator('[data-target="rt"]').click()
            page.locator('#convertButton').click()
            expect(page.locator('#refreshProgress')).to_be_visible()
            assert page.locator('#inputText').is_disabled()
            page.locator('#stopRefreshButton').click()
            expect(page.locator('#statusMessage')).to_contain_text('联网转换完成')
            assert len(calls) - before <= 2
            assert '未发送' in page.locator('#statusMessage').inner_text()
            assert page.locator('#downloadButton').is_enabled()
            print('PASS: graceful stop completes in-flight refreshes and exports untouched remaining inputs')

            page.set_viewport_size({'width': 390, 'height': 844})
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
            page.screenshot(path=str(ARTIFACTS / 'online-mobile.png'), full_page=True)
            assert not errors, errors
            assert all(not url.startswith('https:') for url in requests)
            assert all(RT1 not in url and RT2 not in url for url in requests)
            print('PASS: mobile layout, no page errors, no direct remote or Token-in-URL requests')

            page.locator('#shutdownButton').click()
            expect(page.locator('#serviceBadge')).to_have_text('本机服务已停止')
            thread.join(timeout=3)
            assert not thread.is_alive()
            assert page.locator('#downloadButton').is_enabled()
            print('PASS: local shutdown keeps already-generated downloads available')
            browser.close()
    finally:
        service.shutdown()
        service.server_close()


if __name__ == '__main__':
    main()
