#!/usr/bin/env python3
"""Capture public documentation images with fictional accounts and mock OAuth."""
from __future__ import annotations

import base64
import json
from pathlib import Path
import sys
import threading
from unittest.mock import patch

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import server


def jwt(claims):
    payload = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip('=')
    return f'eyJhbGciOiJub25lIn0.{payload}.DEMO_SIGNATURE'


def demo_refresh(options):
    token = options['refresh_token']
    if not token.startswith('rt_DEMO_ONLY_screenshot_'):
        raise ValueError('Only fictional screenshot credentials are accepted')
    suffix = token[-3:]
    return {
        'access_token': jwt({'exp': 1893456000, 'https://api.openai.com/auth': {
            'chatgpt_account_id': 'demo-account-' + suffix, 'chatgpt_plan_type': 'plus'},
            'https://api.openai.com/profile': {'email': f'demo{suffix}@example.com'}}),
        'id_token': jwt({'email': f'demo{suffix}@example.com'}),
        'refresh_token': 'rt_DEMO_ONLY_screenshot_rotated_' + suffix,
        'client_id': options['client_id'],
        'last_refresh': '2026-01-01T00:00:00Z', 'expires_at': '2030-01-01T00:00:00Z',
    }


def main():
    output = ROOT / 'docs' / 'images'
    output.mkdir(parents=True, exist_ok=True)
    service = server.ConverterServer(('127.0.0.1', 0), demo_refresh)
    thread = threading.Thread(target=service.serve_forever, daemon=True)
    thread.start()
    errors = []
    requests = []
    try:
        # Documentation never reads or displays the capture machine's proxy.
        with patch.object(server, 'resolve_proxy', return_value=''), sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel='chrome', headless=True)
            page = browser.new_page(viewport={'width': 1440, 'height': 1080}, device_scale_factor=1)
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.on('request', lambda request: requests.append(request.url))
            base = f'http://127.0.0.1:{service.server_port}'
            page.goto(base)
            expect(page.locator('#serviceBadge')).to_have_text('本机服务已连接')
            expect(page.locator('#serviceNote')).to_contain_text('直接连接')
            page.evaluate('document.fonts.ready')
            page.screenshot(path=str(output / 'desktop.png'), full_page=True)

            page.locator('[data-target="cpa"]').click()
            page.locator('#inputText').fill('rt_DEMO_ONLY_screenshot_token_001\nrt_DEMO_ONLY_screenshot_token_002')
            page.locator('#convertButton').click()
            expect(page.locator('#statusMessage')).to_contain_text('2 个账号刷新成功')
            expect(page.locator('#outputCount')).to_contain_text('2 个账号')
            expect(page.locator('#downloadButton')).to_be_enabled()
            assert '••••' in page.locator('#outputText').input_value()
            page.locator('#detailsButton').click()
            page.screenshot(path=str(output / 'conversion.png'), full_page=True)
            assert not errors, errors
            assert all(url.startswith(base + '/') for url in requests)
            browser.close()
    finally:
        service.shutdown()
        service.server_close()
        thread.join(timeout=3)
    print('Created docs/images/desktop.png and docs/images/conversion.png (demo data only)')


if __name__ == '__main__':
    main()
