"""Capture the actual local Grafana UI after the synthetic workload runs."""
from pathlib import Path
from playwright.sync_api import sync_playwright

with sync_playwright() as pw:
    browser=pw.chromium.launch()
    page=browser.new_page(viewport={'width':1500,'height':1400})
    page.goto('http://127.0.0.1:3000/d/waterloo-journey/journey?from=now-5m&to=now&kiosk',wait_until='domcontentloaded')
    page.get_by_text('Navigation p95 seconds',exact=True).first.wait_for(timeout=60000)
    page.wait_for_selector('canvas',timeout=30000)
    Path('reports').mkdir(exist_ok=True)
    page.screenshot(path='reports/grafana-synthetic.png',full_page=True)
    browser.close()
