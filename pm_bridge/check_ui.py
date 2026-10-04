"""Browser smoke check with isolated settings and no sync worker."""
import os
import subprocess
import time
from pathlib import Path
import requests
from playwright.sync_api import sync_playwright

env = {**os.environ, 'PM_BRIDGE_DATA_DIR': str(Path(os.environ['LOCALAPPDATA']) / 'Temp' / 'opencode' / 'ui-review')}
process = subprocess.Popen(['py', '-3.11', '-m', 'uvicorn', 'pm_bridge.web_app:app', '--port', '5566'],
                           env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
try:
    for _ in range(60):
        try:
            if requests.get('http://127.0.0.1:5566').status_code == 200:
                break
        except requests.RequestException:
            pass
        time.sleep(.3)
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={'width': 1440, 'height': 1100})
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.goto('http://127.0.0.1:5566')
        page.wait_for_function('ready === true')
        page.screenshot(path='pm_bridge/ui-dashboard.png', full_page=True)
        page.locator('[data-page=folders]').click()
        page.locator('#addFolder').click()
        assert page.locator('#folderInputs input').count() == 2
        page.locator('#folderInputs button').last.click()
        assert page.locator('#folderInputs input').count() == 1
        page.locator('[data-page=settings]').click()
        page.locator('#sync_interval_seconds').fill('180')
        page.locator('#settingsForm button[type=submit]').click()
        page.wait_for_function("settings.sync_interval_seconds === '180'")
        page.reload()
        page.wait_for_function('ready === true')
        assert page.locator('#sync_interval_seconds').input_value() == '180'
        page.set_viewport_size({'width': 390, 'height': 844})
        page.locator('[data-page=overview]').click()
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), 'mobile overflow'
        assert not errors, errors
        print('UI PASS: navigation, folders add/remove, settings save/reload, mobile layout, no JS errors')
        browser.close()
finally:
    process.terminate()
    process.wait()
