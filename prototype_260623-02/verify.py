#!/usr/bin/env python3
"""Headless verify for combined multiview×OOS prototype. Throwaway."""
import subprocess, time, sys, signal
from pathlib import Path
from playwright.sync_api import sync_playwright

HERE = Path(__file__).parent
PORT = 8769
URL = f"http://localhost:{PORT}/index.html"

srv = subprocess.Popen(["python3", "serve.py", str(PORT)], cwd=str(HERE),
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
time.sleep(1.2)
issues = []
try:
    with sync_playwright() as p:
        b = p.chromium.launch()
        pg = b.new_page(viewport={"width": 1400, "height": 900})
        errs = []
        pg.on("console", lambda m: errs.append(m.text) if m.type == "error" else None)
        pg.on("pageerror", lambda e: errs.append(str(e)))
        pg.goto(URL, wait_until="load")
        pg.wait_for_function(
            "typeof DATA!=='undefined' && document.querySelectorAll('#cmpTable tbody tr').length>0"
            " && document.querySelectorAll('#chart canvas').length>0", timeout=10000)
        time.sleep(1.0)
        (HERE / "shots").mkdir(exist_ok=True)

        # compare tab (default): verdict + 比較表(左)＋グラフ(右)
        verdict = pg.text_content("#cmpVerdict .big")
        vbadge = pg.text_content("#vBadge")
        cmp = pg.evaluate("""()=>{
          const rows=[...document.querySelectorAll('#cmpTable tbody tr')];
          const grp=rows.filter(r=>r.classList.contains('grp')).length;
          const data=rows.filter(r=>!r.classList.contains('grp')).length;
          const tnp=rows.find(r=>/Total Net Profit/.test(r.innerText));
          const left=document.querySelector('.cmp-left').getBoundingClientRect();
          const right=document.querySelector('.cmp-right').getBoundingClientRect();
          const eq=document.querySelector('#cmpEquity')?1:0, pnl=document.querySelector('#cmpPnl')?1:0;
          return {grp, data, tnp: tnp?[...tnp.children].map(c=>c.innerText):null,
            split: right.x>left.x+left.width-5, eq, pnl, rightW:Math.round(right.width)};
        }""")
        print("verdict:", verdict, "| badge:", vbadge, "| groups:", cmp["grp"], "| metric rows:", cmp["data"],
              "| charts right-of-table:", cmp["split"], "| right width:", cmp["rightW"])
        print("Total Net Profit row:", cmp["tnp"])
        if cmp["data"] < 50:
            issues.append(f"compare metric rows too few (got {cmp['data']}, expect ~54 全指標)")
        if cmp["grp"] < 8:
            issues.append(f"compare group headers too few (got {cmp['grp']})")
        if not cmp["split"]:
            issues.append("charts not placed to the right of the table (split layout broken)")
        if not (cmp["eq"] and cmp["pnl"]):
            issues.append("equity/pnl charts missing in right column")
        if not cmp["tnp"] or cmp["tnp"][3] != "-0.354":
            issues.append(f"Total Net Profit row wrong: {cmp['tnp']}")
        if "過剰最適化" not in (verdict or ""):
            issues.append(f"verdict not 過剰最適化: {verdict}")

        # IS multiview (default segment)
        pg.eval_on_selector(".tab[data-pane=detail]", "el=>el.click()"); time.sleep(0.4)
        is_rows = pg.eval_on_selector_all("#tradeTable tbody tr", "els=>els.length")
        is_meta = pg.text_content("#hMeta")
        print("IS detail rows:", is_rows, "|", is_meta.strip())
        if is_rows != 5224:
            issues.append(f"IS detail rows != 5224 (got {is_rows})")

        # switch to OOS, verify segment data swapped
        pg.eval_on_selector(".segbtn[data-seg=oos]", "el=>el.click()"); time.sleep(0.8)
        seg = pg.evaluate("()=>SEG")
        oos_rows = pg.eval_on_selector_all("#tradeTable tbody tr", "els=>els.length")
        oos_meta = pg.text_content("#hMeta")
        print("after OOS switch: SEG=", seg, "detail rows:", oos_rows, "|", oos_meta.strip())
        if seg != "oos":
            issues.append(f"segment did not switch to oos (SEG={seg})")
        if oos_rows != 2438:
            issues.append(f"OOS detail rows != 2438 (got {oos_rows})")

        # OOS graphs render
        pg.eval_on_selector(".tab[data-pane=graphs]", "el=>el.click()"); time.sleep(0.6)
        gcanv = pg.eval_on_selector_all("#graphGrid canvas", "els=>els.length")
        print("OOS graph canvases:", gcanv)
        if gcanv != 11:
            issues.append(f"OOS graph canvases != 11 (got {gcanv})")

        # back to compare for screenshot
        pg.eval_on_selector(".segbtn[data-seg=is]", "el=>el.click()"); time.sleep(0.4)
        pg.eval_on_selector(".tab[data-pane=compare]", "el=>el.click()"); time.sleep(0.4)
        pg.screenshot(path=str(HERE / "shots" / "verify.png"))

        if errs:
            issues.append(f"console errors: {errs}")
        b.close()
finally:
    srv.send_signal(signal.SIGINT)
    srv.wait()

print("\nTOTAL issues:", len(issues))
for i in issues:
    print(" -", i)
sys.exit(1 if issues else 0)
