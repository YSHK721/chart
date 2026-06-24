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
          const eq=document.querySelector('#cmpEquity')?1:0, pnl=document.querySelector('#cmpPnl')?1:0, dd=document.querySelector('#cmpDD')?1:0;
          const ddLabs=(typeof cmpCharts!=='undefined'&&cmpCharts.dd)?cmpCharts.dd.data.datasets.map(d=>d.label):[];
          return {grp, data, tnp: tnp?[...tnp.children].map(c=>c.innerText):null,
            split: right.x>left.x+left.width-5, eq, pnl, dd, ddLabs, rightW:Math.round(right.width)};
        }""")
        print("verdict:", verdict, "| badge:", vbadge, "| groups:", cmp["grp"], "| metric rows:", cmp["data"],
              "| charts right-of-table:", cmp["split"], "| right width:", cmp["rightW"])
        print("Total Net Profit row:", cmp["tnp"])
        print("DD labels:", cmp["ddLabs"])
        if cmp["data"] < 50:
            issues.append(f"compare metric rows too few (got {cmp['data']}, expect ~54 全指標)")
        if cmp["grp"] < 8:
            issues.append(f"compare group headers too few (got {cmp['grp']})")
        if not cmp["split"]:
            issues.append("charts not placed to the right of the table (split layout broken)")
        if not (cmp["eq"] and cmp["pnl"] and cmp["dd"]):
            issues.append("equity/pnl/drawdown charts missing in right column")
        if not any("-7.97%" in s for s in cmp["ddLabs"]):
            issues.append(f"DD chart IS max-DD label wrong (expect -7.97%): {cmp['ddLabs']}")
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

        # chart: Balance/Drawdown は独立ウィンドウ（別チャート）で時間軸同期
        base = pg.evaluate("""()=>{
          const sep = typeof balChart!=='undefined'&&!!balChart && typeof ddChart!=='undefined'&&!!ddChart;
          const bc=document.querySelectorAll('#paneBal canvas').length, dc=document.querySelectorAll('#paneDD canvas').length;
          chart.timeScale().setVisibleLogicalRange({from:6000,to:9000});  // pan main
          return {sep, bc, dc};
        }""")
        time.sleep(0.5)  # 同期コールバック反映待ち
        synced = pg.evaluate("""()=>{
          const lb=balChart.timeScale().getVisibleLogicalRange(), ld=ddChart.timeScale().getVisibleLogicalRange();
          return !!(lb && ld && Math.abs(lb.to-9000)<2 && Math.abs(ld.to-9000)<2);
        }""")
        print("separate panes:", base, "| synced to main pan:", synced)
        if not base["sep"]:
            issues.append("Balance/Drawdown separate pane charts missing")
        if not (base["bc"] and base["dc"]):
            issues.append("pane canvases missing")
        if not synced:
            issues.append("pane time axes not synced to main chart logical range")
        pg.evaluate("""()=>{const t0=DATA.trades[0].entry_time,t1=DATA.trades[DATA.trades.length-1].exit_time;
          chart.timeScale().setVisibleRange({from:t0-600,to:t1+600});}""")  # restore

        # resizers present + draggable (window size adjustable)
        rz = pg.evaluate("()=>['rz0','rz1','rz2'].every(id=>!!document.getElementById(id))")
        h0 = pg.eval_on_selector("#paneBal", "el=>Math.round(el.getBoundingClientRect().height)")
        box = pg.eval_on_selector("#rz1", "el=>{const r=el.getBoundingClientRect();return {x:r.x+r.width/2,y:r.y+r.height/2};}")
        pg.mouse.move(box["x"], box["y"]); pg.mouse.down(); pg.mouse.move(box["x"], box["y"] + 50, steps=4); pg.mouse.up()
        time.sleep(0.3)
        h1 = pg.eval_on_selector("#paneBal", "el=>Math.round(el.getBoundingClientRect().height)")
        print("resizers present:", rz, "| paneBal height", h0, "->", h1, "after rz1 drag")
        if not rz:
            issues.append("resizers (rz0/rz1/rz2) missing")
        if abs(h1 - h0) < 30:
            issues.append(f"window not resizable via rz1 (height {h0}->{h1})")

        # crosshair sync across windows (hover candle, no throw; sync handler wired)
        cb = pg.eval_on_selector("#chart canvas", "el=>{const r=el.getBoundingClientRect();return {x:r.x+r.width*0.55,y:r.y+r.height*0.5};}")
        pg.mouse.move(cb["x"], cb["y"], steps=3)
        time.sleep(0.3)

        # graphs tab: 残り9グラフが IS/OOS 比較（2系列）。Balance/DD は移設済み。
        pg.eval_on_selector(".tab[data-pane=graphs]", "el=>el.click()"); time.sleep(0.6)
        gcanv = pg.eval_on_selector_all("#graphGrid canvas", "els=>els.length")
        two = pg.evaluate("""()=>{
          const keys=['eh','ew','em','ph','pw','pm','cf','ca','ht'];
          return {all2:keys.every(k=>charts[k]&&charts[k].data.datasets.length===2),
                  noBalDD: !charts.bal && !charts.dd};
        }""")
        print("graph canvases:", gcanv, "| 9 graphs 2-dataset(IS/OOS):", two["all2"], "| bal/dd moved out:", two["noBalDD"])
        if gcanv != 9:
            issues.append(f"graph canvases != 9 (got {gcanv}; Balance/DD should be moved to chart)")
        if not two["all2"]:
            issues.append("not all graphs compare IS/OOS (expected 2 datasets each)")
        if not two["noBalDD"]:
            issues.append("Balance/Drawdown still in Graphs tab (should be chart overlay panes)")

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
