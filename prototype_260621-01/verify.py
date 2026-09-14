#!/usr/bin/env python3
"""Drive the prototype with a headless browser, exercise linkage, capture screenshots."""
import sys
from playwright.sync_api import sync_playwright

URL = "http://localhost:8765/index.html"
OUT = "/workspaces/app/prototype_260621-01/shots"
import os; os.makedirs(OUT, exist_ok=True)
errors = []

with sync_playwright() as p:
    b = p.chromium.launch()
    pg = b.new_page(viewport={"width": 1480, "height": 940})
    pg.on("console", lambda m: errors.append(f"[{m.type}] {m.text}") if m.type in ("error","warning") else None)
    pg.on("pageerror", lambda e: errors.append(f"[pageerror] {e}"))
    pg.goto(URL)
    pg.wait_for_function("typeof DATA!=='undefined' && typeof chart!=='undefined' && document.querySelectorAll('tr.tw').length>0", timeout=20000)
    pg.wait_for_timeout(900)

    # 1. default chart + trade detail (markers should be visible now)
    rows = pg.eval_on_selector_all("tr.tw", "els=>els.length")
    badge = pg.text_content("#chartBadge")
    pg.screenshot(path=f"{OUT}/01_detail.png")
    print("rows:", rows, "| badge:", badge)
    if "0 trades" in (badge or ""): errors.append("default view shows 0 trades")

    # 2. hover a trade row -> expect chart selection text + marker highlight
    pg.hover("tr.tw[data-id='30']")
    pg.wait_for_timeout(400)
    sel = pg.text_content("#hSel")
    print("hover#30 hSel:", sel)
    pg.screenshot(path=f"{OUT}/02_rowhover.png")
    if "#30" not in (sel or ""): errors.append("row hover did not set selection")

    # 3. heatmap tab + click a cell
    pg.click("div.tab[data-pane='heat']")
    pg.wait_for_timeout(300)
    pg.screenshot(path=f"{OUT}/03_heatmap.png")
    cell = pg.query_selector("#heatHost td.cell")
    cell.click()
    pg.wait_for_timeout(400)
    cnt = pg.text_content("#detailCount")
    print("heat click filter:", cnt)
    pg.screenshot(path=f"{OUT}/04_heat_filter.png")

    # 4. graphs tab
    pg.evaluate("applyFilter(null)")
    pg.click("div.tab[data-pane='graphs']")
    pg.wait_for_timeout(700)
    canv = pg.eval_on_selector_all("canvas", "els=>els.length")
    print("canvases:", canv)
    pg.screenshot(path=f"{OUT}/05_graphs.png")

    # 5. click a scatter point (MFE) -> selection
    box = pg.query_selector("#gCF").bounding_box()
    pg.mouse.click(box["x"]+box["width"]*0.6, box["y"]+box["height"]*0.4)
    pg.wait_for_timeout(300)
    print("after scatter click hSel:", pg.text_content("#hSel"))

    # 6. report tab
    pg.click("div.tab[data-pane='report']")
    pg.wait_for_timeout(200)
    rr = pg.eval_on_selector_all(".rrow", "e=>e.length")
    print("report rows:", rr)
    pg.screenshot(path=f"{OUT}/06_report.png")

    # 7. zoom out chart fully to test marker cap
    pg.evaluate("chart.timeScale().fitContent()")
    pg.wait_for_timeout(500)
    pg.click("div.tab[data-pane='detail']")
    print("full-zoom badge:", pg.text_content("#chartBadge"))
    pg.screenshot(path=f"{OUT}/07_fullzoom.png")

    b.close()

print("\n=== console errors/warnings ===")
for e in errors[:40]: print(e)
print("TOTAL issues:", len(errors))
sys.exit(0)
