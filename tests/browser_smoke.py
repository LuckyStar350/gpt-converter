"""Optional developer UI check. Requires Playwright and an installed Chrome/Edge.

The bundled Windows application needs no external Python or Playwright.
Run: python tests/browser_smoke.py
"""
import json
from pathlib import Path
import zipfile
import sys
import threading
import base64

from playwright.sync_api import sync_playwright, expect

ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS = ROOT / '.test-artifacts'
ARTIFACTS.mkdir(exist_ok=True)
RT = 'rt_DEMO_ONLY_not_a_real_token_001'
sys.path.insert(0, str(ROOT))
import server


def smoke_refresh(options):
    claims = {'exp': 1893456000, 'https://api.openai.com/auth': {'chatgpt_account_id': 'test-account-' + options['refresh_token'][-3:]}}
    encoded = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip('=')
    return {'access_token': f'eyJhbGciOiJub25lIn0.{encoded}.DEMO_SIGNATURE',
            'refresh_token': options['refresh_token'], 'client_id': options['client_id'],
            'last_refresh': '2026-10-11T00:00:00Z', 'expires_at': '2030-01-01T00:00:00Z'}


def main():
    service = server.ConverterServer(('127.0.0.1', 0), smoke_refresh)
    thread = threading.Thread(target=service.serve_forever, daemon=True)
    thread.start()
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel='chrome', headless=True)
        context = browser.new_context(viewport={'width': 1440, 'height': 1080}, accept_downloads=True, permissions=['clipboard-read', 'clipboard-write'])
        page = context.new_page()
        errors = []
        console_errors = []
        remote_requests = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.on('console', lambda event: console_errors.append(event.text) if event.type == 'error' else None)
        page.on('request', lambda request: remote_requests.append(request.url) if request.url.startswith('https:') else None)
        page.goto(f'http://127.0.0.1:{service.server_port}')
        expect(page.locator('#serviceBadge')).to_have_text('本机服务已连接')
        assert page.evaluate('Boolean(window.GPTConverter && window.LocalArchive)')
        assert page.locator('#downloadButton').is_disabled()
        assert page.locator('#emptyOutput').is_visible()
        page.screenshot(path=str(ARTIFACTS / 'desktop-empty.png'), full_page=True)

        # Paste/sample flow, keyboard shortcut, six format directions, masked preview.
        for source in ('rt', 'sub2', 'cpa'):
            for target in ('rt', 'sub2', 'cpa'):
                if source == target:
                    continue
                page.locator(f'[data-example="{source}"]').click()
                page.locator(f'[data-target="{target}"]').click()
                page.locator('#inputText').focus()
                page.keyboard.press('Control+Enter')
                expect(page.locator('#statusMessage')).to_contain_text('联网转换完成')
                assert page.locator('#downloadButton').is_enabled(), (source, target)
                assert RT not in page.locator('#outputText').input_value()
                page.locator('#revealButton').click()
                assert RT in page.locator('#outputText').input_value()
        print('PASS: all six UI conversion directions, shortcut, preview masking')

        # Batch CPA import, ZIP download, and independent Python ZIP verification.
        page.locator('#clearButton').click()
        page.locator('#fileInput').set_input_files([str(ROOT / 'examples/cpa/demo-001.json'), str(ROOT / 'examples/cpa/demo-002.json')])
        expect(page.locator('#convertButton')).to_be_enabled()
        page.locator('[data-target="cpa"]').click()
        page.locator('#convertButton').click()
        expect(page.locator('#statusMessage')).to_contain_text('联网转换完成')
        assert '2 个账号' in page.locator('#outputCount').inner_text()
        page.locator('#copyButton').click()
        copied = json.loads(page.evaluate('() => navigator.clipboard.readText()'))
        assert copied['refresh_token'] == RT
        page.locator('#outputFilename select').select_option('1')
        assert 'demo002@example.com' in page.locator('#outputText').input_value()
        with page.expect_download() as download_info:
            page.locator('#downloadButton').click()
        zip_path = ARTIFACTS / download_info.value.suggested_filename
        download_info.value.save_as(str(zip_path))
        with zipfile.ZipFile(zip_path) as downloaded:
            assert downloaded.testzip() is None
            assert len(downloaded.namelist()) == 2
            outputs = [json.loads(downloaded.read(name)) for name in downloaded.namelist()]
            assert {item['refresh_token'] for item in outputs} == {RT, 'rt_DEMO_ONLY_not_a_real_token_002'}
        print('PASS: multiple files, CPA preview switching, actual ZIP download, Python verification')
        page.screenshot(path=str(ARTIFACTS / 'desktop-result.png'), full_page=True)

        # Stored ZIP import, Deflate ZIP import, and directory import.
        deflated_path = ARTIFACTS / 'deflated-input.zip'
        with zipfile.ZipFile(deflated_path, 'w', compression=zipfile.ZIP_DEFLATED) as fixture:
            fixture.write(ROOT / 'examples/cpa/demo-001.json', 'nested/demo-001.json')
            fixture.write(ROOT / 'examples/cpa/demo-002.json', 'nested/demo-002.json')
            fixture.writestr('readme.md', 'not an account')
        for source in (zip_path, deflated_path):
            page.locator('#clearButton').click()
            page.locator('#fileInput').set_input_files(str(source))
            expect(page.locator('#convertButton')).to_be_enabled()
            page.locator('[data-target="sub2"]').click()
            page.locator('#convertButton').click()
            expect(page.locator('#statusMessage')).to_contain_text('联网转换完成')
            assert '2 个账号' in page.locator('#outputCount').inner_text(), source
        page.locator('#clearButton').click()
        page.locator('#folderInput').set_input_files(str(ROOT / 'examples/cpa'))
        expect(page.locator('#convertButton')).to_be_enabled()
        page.locator('#convertButton').click()
        expect(page.locator('#statusMessage')).to_contain_text('联网转换完成')
        assert '2 个账号' in page.locator('#outputCount').inner_text()
        print('PASS: Store ZIP, Deflate ZIP and folder imports')

        # Sub2 importable output is complete even while its preview is redacted.
        with page.expect_download() as download_info:
            page.locator('#downloadButton').click()
        json_path = ARTIFACTS / download_info.value.suggested_filename
        download_info.value.save_as(str(json_path))
        payload = json.loads(json_path.read_text(encoding='utf-8'))
        assert payload['type'] == 'sub2api-data'
        assert payload['accounts'][0]['credentials']['refresh_token'] == RT
        print('PASS: actual Sub2API JSON download contains original tokens')

        # Stale outputs cannot be downloaded after editing, and malformed JSON is diagnosed.
        page.locator('#inputText').fill('{"broken":')
        assert page.locator('#downloadButton').is_disabled()
        page.locator('#clearButton').click()
        page.locator('#inputText').fill('{"broken":')
        page.locator('#convertButton').click()
        expect(page.locator('#reportDetails')).to_contain_text('JSON 语法错误')
        assert page.locator('#downloadButton').is_disabled()
        assert 'JSON 语法错误' in page.locator('#reportDetails').inner_text()
        print('PASS: invalid input diagnostics and stale-download prevention')

        # Pasted account names are text, not markup. Clearing and reloading remove state.
        page.locator('#clearButton').click()
        attack = {'type': 'codex', 'refresh_token': RT, 'email': '<img src=x onerror=alert(1)>'}
        page.locator('#inputText').fill(json.dumps(attack))
        page.locator('#convertButton').click()
        expect(page.locator('#statusMessage')).to_contain_text('联网转换完成')
        page.locator('#detailsButton').click()
        assert page.locator('#accountRows img').count() == 0
        assert '<img' in page.locator('#accountRows').inner_text()
        page.locator('#helpButton').click()
        assert page.locator('#helpDialog').is_visible()
        page.keyboard.press('Escape')
        assert not page.locator('#helpDialog').is_visible()
        page.reload()
        assert page.locator('#inputText').input_value() == ''
        assert page.evaluate('localStorage.length') == 0
        assert page.evaluate('sessionStorage.length') == 0
        print('PASS: safe text rendering, help dialog and zero persistent account state')

        # Mobile layout has no page overflow, and remains usable with the local bridge.
        page.set_viewport_size({'width': 390, 'height': 844})
        page.locator('[data-example="cpa"]').click()
        page.locator('[data-target="sub2"]').click()
        page.locator('#convertButton').click()
        expect(page.locator('#statusMessage')).to_contain_text('联网转换完成')
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        page.screenshot(path=str(ARTIFACTS / 'mobile-result.png'), full_page=True)
        print('PASS: mobile viewport, default online conversion')
        assert not errors, errors
        assert not console_errors, console_errors
        assert not remote_requests, remote_requests
        print('PASS: zero JavaScript/console errors and zero direct remote browser requests')
        page.goto((ROOT / 'index.html').as_uri())
        page.locator('#inputText').fill(RT)
        page.locator('#convertButton').click()
        expect(page.locator('#statusMessage')).to_contain_text('启动工具.bat')
        assert page.locator('#downloadButton').is_disabled()
        print('PASS: direct HTML opening guides the user to launch the service')
        browser.close()
    service.shutdown()
    service.server_close()
    thread.join(timeout=2)


if __name__ == '__main__':
    main()
