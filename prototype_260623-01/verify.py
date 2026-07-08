#!/usr/bin/env python3
"""Headless verify for OOS white-check prototype. Throwaway."""
import subprocess, time, sys, os, signal
from pathlib import Path
from playwright.sync_api import sync_playwright

HERE = Path(__file__).parent
PORT = 8767
URL = f"http://localhost:{PORT}/index.html"

srv = subprocess.Popen(["python3", "-m", "http.server", str(PORT)],
                       cwd=str(HERE), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
time.sleep(1.2)
issues = []
try:
    with sync_playwright() as p:
        b = p.chromium.launch()
        pg = b.new_page()
        errs = []
        pg.on("console", lambda m: errs.append(m.text) if m.type == "error" else None)
        pg.on("pageerror", lambda e: errs.append(str(e)))
        pg.goto(URL)
        pg.wait_for_function(
            "typeof DATA!=='undefined' && typeof lwChart!=='undefined'"
            " && document.querySelectorAll('#degtable tbody tr').length>0"
            " && document.querySelectorAll('#chart canvas').length>0", timeout=10000)
        time.sleep(1.0)
        (HERE / "shots").mkdir(exist_ok=True)
        # observe values
        verdict = pg.text_content("#verdict .badge")
        # チャート: 全期間が収まり建玉総数(7662)が badge に出ること
        full_badge = pg.text_content("#chartBadge")
        rng = pg.evaluate("()=>lwChart.timeScale().getVisibleRange()")
        span_days = (rng["to"] - rng["from"]) / 86400 if rng else 0
        chart_canvases = pg.eval_on_selector_all("#chart canvas", "els=>els.map(e=>e.width+'x'+e.height)")
        print("chart full badge:", full_badge, "| span days:", round(span_days, 1))
        print("chart canvases:", chart_canvases)
        if span_days < 30:
            issues.append(f"price chart not full-period (span {span_days:.1f}d < 30)")
        if "7662" not in (full_badge or ""):
            issues.append(f"chart badge missing total markers: {full_badge}")
        rows = pg.eval_on_selector_all("#degtable tbody tr", "els=>els.map(e=>e.innerText)")
        oracle = pg.eval_on_selector_all("#oracle tbody tr", "els=>els.map(e=>e.innerText)")
        canvases = pg.eval_on_selector_all("canvas", "els=>els.map(e=>e.width+'x'+e.height)")
        pg.screenshot(path=str(HERE / "shots" / "dashboard.png"), full_page=True)
        print("verdict badge:", verdict)
        print("degradation rows:", len(rows))
        for r in rows: print("  ", r.replace("\n", " | "))
        print("oracle rows:")
        for r in oracle: print("  ", r.replace("\n", " | "))
        print("canvases:", canvases)
        if not verdict or verdict.strip() == "":
            issues.append("verdict badge empty")
        if len(rows) != 7:
            issues.append(f"degradation rows != 7 (got {len(rows)})")
        for c in canvases:
            if c.startswith("0x") or c.endswith("x0"):
                issues.append(f"canvas not sized: {c}")
        if errs:
            issues.append(f"console errors: {errs}")
        b.close()
finally:
    srv.send_signal(signal.SIGINT)
    srv.wait()

print("\nTOTAL issues:", len(issues))
for i in issues: print(" -", i)
sys.exit(1 if issues else 0)
