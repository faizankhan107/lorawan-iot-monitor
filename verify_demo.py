"""Verify the running Compose demo; all readings are simulated MQTT events."""
import json
import math
import subprocess
import time
import urllib.request
from pathlib import Path
from playwright.sync_api import sync_playwright

BASE = 'http://127.0.0.1:8000'
EXPECTED = {f'a84041aabbccdd0{i}' for i in range(1, 4)}

def get(path):
    with urllib.request.urlopen(BASE + path, timeout=10) as response:
        assert response.status == 200
        return json.load(response)

def compose(*args):
    subprocess.run(['docker', 'compose', *args], check=True)

def wait_ready():
    for _ in range(30):
        try:
            if get('/health') == {'status': 'ok'}:
                return
        except (OSError, ValueError):
            pass
        time.sleep(1)
    raise AssertionError('Dashboard did not become ready')

def main():
    wait_ready()
    with sync_playwright() as p:
        browser = p.chromium.launch(channel='msedge', headless=True)
        page = browser.new_page()
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        assert page.goto(BASE).status == 200
        page.wait_for_function("document.querySelectorAll('article').length === 3")
        rows = get('/api/devices')
        assert {r['dev_eui'] for r in rows} == EXPECTED
        for r in rows:
            assert r['source'] == 'simulated' and r['status'] == 'online'
            assert math.isfinite(r['temperature_c']) and 20 <= r['temperature_c'] <= 26
            assert math.isfinite(r['humidity_pct']) and 41 <= r['humidity_pct'] <= 57
            i = int(r['dev_eui'][-1]) - 1
            assert r['rssi'] == -65 - 9 * i and r['snr'] == 8 - 2 * i
        print('ONLINE readings: ' + json.dumps(rows), flush=True)
        time.sleep(12)
        for eui in sorted(EXPECTED):
            history = get('/api/history/' + eui)
            assert len(history) >= 2
        page.screenshot(path='verification-online.png', full_page=True)
        compose('--profile', 'demo', 'stop', 'simulator')
        stopped = time.time()
        frozen = get('/api/devices')
        histories = {eui: get('/api/history/' + eui) for eui in sorted(EXPECTED)}
        print('Simulator stopped; saved history counts: ' + json.dumps({e: len(h) for e, h in histories.items()}), flush=True)
        compose('up', '-d', '--no-deps', '--force-recreate', 'monitor')
        wait_ready()
        assert histories == {eui: get('/api/history/' + eui) for eui in sorted(EXPECTED)}
        assert [r['id'] for r in frozen] == [r['id'] for r in get('/api/devices')]
        print('PASS: history and latest IDs survived monitor container recreation', flush=True)
        while time.time() < stopped + 92:
            rows = get('/api/devices')
            now = int(time.time())
            for r in rows:
                age = now - r['received_at']
                if age <= 89:
                    assert r['status'] == 'online'
                if age >= 92:
                    assert r['status'] == 'stale'
            print('Freshness: ' + json.dumps([{ 'device': r['dev_eui'], 'age_s': now-r['received_at'], 'status': r['status']} for r in rows]), flush=True)
            time.sleep(5)
        assert all(r['status'] == 'stale' for r in get('/api/devices'))
        page.wait_for_function("[...document.querySelectorAll('article .badge')].length === 3 && [...document.querySelectorAll('article .badge')].every(e => e.textContent === 'stale')", timeout=15000)
        assert histories == {eui: get('/api/history/' + eui) for eui in sorted(EXPECTED)}
        assert not errors, errors
        page.screenshot(path='verification-stale.png', full_page=True)
        print('PASS: dashboard rendered three stale simulated devices; history unchanged; no JavaScript errors', flush=True)
        browser.close()

if __name__ == '__main__':
    main()
