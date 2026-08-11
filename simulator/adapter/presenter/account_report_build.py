"""report_build — 口座状態時系列 JSON → グラフ入り単体 HTML レポート（ISSUE-369・アクター 2）。

使い方:
    python report_build.py --in out/long_stop.json --out out/long_stop.html [--title 名前]

責務: 表示のみ（計算しない・SRP）。simulator/tools/run_account_scenario.py の出力 JSON を埋め込み、ブラウザで
開くだけで確認できる自己完結 HTML を生成する（サーバ不要・外部ライブラリ不要）。
グラフ 4 枚:
    1. 価格（bid）＋ 建玉・損切り・利確・実測ロスカット水準 ＋ 約定イベント
    2. 口座残高・有効証拠金
    3. 必要証拠金（時価ベースで変動する実測）
    4. 証拠金維持率 ＋ ロスカット水準（100%）
イベント表と、エンジンの未検証事項（U1〜U4）を必ず併記する。
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

_PAGE = """<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>__TITLE__ — 口座状態レポート</title>
<style>
  :root{
    --bg:#0F141C; --panel:#1A2230; --ink:#E6E9EF; --muted:#8C96A8;
    --line:#2A3340; --grid:#222B39;
    --price:#B5D4F4; --equity:#E0A94A; --balance:#7C8698; --margin:#AE9FE0;
    --ratio:#34B98D; --danger:#EC6152; --safe:#34B98D; --entry:#E6E9EF; --tp:#34B98D;
    --mono: ui-monospace,"SFMono-Regular",Menlo,monospace;
    --sans: ui-sans-serif,system-ui,"Hiragino Sans","Noto Sans JP",sans-serif;
  }
  *{box-sizing:border-box;}
  html,body{margin:0;background:var(--bg);color:var(--ink);font-family:var(--sans);line-height:1.5;}
  .wrap{max-width:1060px;margin:0 auto;padding:26px 18px 80px;}
  header{border-bottom:2px solid var(--ink);padding-bottom:14px;margin-bottom:18px;}
  .kicker{font-family:var(--mono);font-size:11px;letter-spacing:.16em;text-transform:uppercase;color:var(--muted);margin:0 0 6px;}
  h1{font-size:21px;margin:0;font-weight:650;}
  .panel{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:18px;margin-bottom:16px;}
  .panel-title{font-family:var(--mono);font-size:13px;letter-spacing:.1em;text-transform:uppercase;color:var(--muted);margin:0 0 12px;}
  canvas{width:100%;display:block;}
  .legend{display:flex;flex-wrap:wrap;gap:14px;font-family:var(--mono);font-size:11px;margin:0 0 10px;color:var(--muted);}
  .legend i{display:inline-block;width:13px;height:3px;vertical-align:middle;margin-right:5px;}
  .cards{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin-bottom:16px;}
  @media(max-width:640px){.cards{grid-template-columns:1fr 1fr;}}
  .card{border:1px solid var(--line);border-radius:9px;padding:12px 13px;background:#141B26;}
  .card .dl{font-family:var(--mono);font-size:10px;letter-spacing:.06em;text-transform:uppercase;color:var(--muted);}
  .card .dv{font-family:var(--mono);font-size:20px;font-weight:700;margin-top:4px;}
  .card .dv.neg{color:var(--danger);} .card .dv.pos{color:var(--safe);}
  table{width:100%;border-collapse:collapse;font-family:var(--mono);font-size:12px;color:var(--ink);}
  th,td{text-align:right;padding:7px 6px;border-bottom:1px solid var(--line);}
  th:first-child,td:first-child,th:nth-child(2),td:nth-child(2){text-align:left;}
  thead th{color:var(--muted);font-weight:500;font-size:10.5px;}
  .notes{font-size:12.5px;color:var(--muted);line-height:1.85;}
  .notes b{color:var(--ink);}
  .flag{border-left:3px solid var(--danger);padding:10px 14px;background:#2A1C1C;border-radius:0 6px 6px 0;margin-top:12px;font-size:12.5px;line-height:1.75;}
  .flag b{color:var(--danger);}
  .chart-cap{font-size:11.5px;color:var(--muted);margin:8px 2px 0;line-height:1.6;}
</style>
</head>
<body>
<div class="wrap">
  <header>
    <p class="kicker">Account State · Tick Granularity · ISSUE-369</p>
    <h1>__TITLE__ — 口座状態レポート</h1>
  </header>
  <div class="cards" id="cards"></div>
  <div class="panel">
    <p class="panel-title">1. 価格と水準（bid）</p>
    <div class="legend" id="lg1"></div>
    <canvas id="c_price" height="300"></canvas>
    <p class="chart-cap">縦線＝約定イベント（実線=entry / 破線=決済）。水平線＝損切り・利確の計画水準。ロスカットは価格ではなく維持率で発動するため、水準線ではなくイベントとして表示。</p>
  </div>
  <div class="panel">
    <p class="panel-title">2. 口座残高・有効証拠金</p>
    <div class="legend"><span><i style="background:var(--balance)"></i>残高（確定）</span><span><i style="background:var(--equity)"></i>有効証拠金（残高＋評価損益）</span></div>
    <canvas id="c_equity" height="240"></canvas>
  </div>
  <div class="panel">
    <p class="panel-title">3. 必要証拠金</p>
    <div class="legend"><span><i style="background:var(--margin)"></i>必要証拠金（<span id="basisdesc"></span>）</span></div>
    <canvas id="c_margin" height="200"></canvas>
    <p class="chart-cap" id="basiscap"></p>
  </div>
  <div class="panel">
    <p class="panel-title">4. 証拠金維持率（有効証拠金÷必要証拠金）</p>
    <div class="legend"><span><i style="background:var(--ratio)"></i>維持率</span><span><i style="background:var(--danger)"></i>ロスカット水準（100%）</span></div>
    <canvas id="c_ratio" height="220"></canvas>
  </div>
  <div class="panel">
    <p class="panel-title">イベント</p>
    <table><thead><tr><th>時刻（UTC）</th><th>種別</th><th>価格</th><th>数量</th><th>確定損益</th><th>備考</th></tr></thead>
    <tbody id="evrows"></tbody></table>
  </div>
  <div class="panel">
    <p class="panel-title">前提（出典: docs/oanda_indices_cfd_about.md ＝ OANDA 証券公式ページ）と未反映事項</p>
    <div class="notes">
      <b>公式仕様どおりの実装:</b> 必要証拠金＝約定代金×証拠金率（§3(2)・<span id="markmode"></span>）。
      有効証拠金＝残高＋評価損益（§3(3) 値洗い）。維持率＝有効証拠金÷必要証拠金×100（§1-2）。
      100% 以下でロスカット・マージンコールなし（§1）。損失の大きい建玉から順に、維持率が
      100% を上回るまで決済（§1-2）。ロスカットは逆指値より優先（§2(9)③）。
      評価価格はロング=bid／ショート=ask（§2(5)）。<br>
      <b>[U3]</b> 損切り・ロスカットの約定価格はトリガー tick の評価価格（成行・すべり＝tick 間
      ギャップのみ）。公式もスリッページと価格非保証を明記するが、板・実スリッページ分布は未反映。<br>
      <b>[U5]</b> ファイナンシングコスト（金利相当額・§2(8)）・配当相当額は未実装。複数日保有では
      実際は日次ロールオーバーで受払が発生する。<br>
      <b>[U6]</b> 公式は「一定の時間間隔で値洗い」（§3(3)）。本実装は毎 tick 判定＝発動が最速側。
    </div>
    <div class="flag" id="lcflag" style="display:none"><b>ロスカット発生:</b> このシナリオでは意図した損切りより先に強制決済が発動している。サイズ（実効 f）の見直しが根本対処。</div>
  </div>
</div>
<script>
const DATA = __DATA__;
const S = DATA.series, EV = DATA.events, META = DATA.meta, SUM = DATA.summary;
const CSS = getComputedStyle(document.documentElement);
const col = n => CSS.getPropertyValue('--' + n).trim();
const fmtT = ms => { const d = new Date(ms); return d.toISOString().slice(5,16).replace('T',' '); };
const fmtY = v => '¥' + Math.round(v).toLocaleString();

// ---- summary cards ----
(function(){
  const plan = META.plan;
  const pnl = SUM.final_balance - plan.balance;
  document.getElementById('cards').innerHTML =
    `<div class="card"><div class="dl">初期残高 → 最終残高</div><div class="dv">${fmtY(plan.balance)}<br>→ ${fmtY(SUM.final_balance)}</div></div>` +
    `<div class="card"><div class="dl">確定損益</div><div class="dv ${pnl>=0?'pos':'neg'}">${pnl>=0?'+':''}${fmtY(pnl)}</div></div>` +
    `<div class="card"><div class="dl">維持率 最小</div><div class="dv ${SUM.losscut_hit?'neg':''}">${(Math.min(...S.margin_ratio.filter(r=>r!=null))*100).toFixed(1)}%</div></div>` +
    `<div class="card"><div class="dl">結末</div><div class="dv ${SUM.losscut_hit?'neg':''}">${SUM.losscut_hit?'ロスカット':SUM.closed?'決済完了':'未決済'}</div></div>`;
  if (SUM.losscut_hit) document.getElementById('lcflag').style.display = '';
  const basis = META.margin_basis || 'entry';
  document.getElementById('markmode').textContent = 'margin_basis=' + basis;
  document.getElementById('basisdesc').textContent =
    basis === 'entry' ? '約定代金×証拠金率・建値固定（公式 §3(2)）' : '時価×数量×証拠金率（比較用 mark 基準）';
  document.getElementById('basiscap').textContent =
    basis === 'entry'
      ? '公式仕様（§3(2)）どおり約定代金基準＝保有中は一定。約定・決済のタイミングでのみ段差が生じる。'
      : '比較用の時価基準（旧計算機 HTML の前提）。公式記載は約定代金基準（§3(2)）。';
})();

// ---- generic line chart ----
function setupCanvas(cv){
  const dpr = window.devicePixelRatio || 1, h = +cv.getAttribute('height');
  cv.style.height = h + 'px'; const w = cv.clientWidth;
  cv.width = w * dpr; cv.height = h * dpr;
  const ctx = cv.getContext('2d'); ctx.scale(dpr, dpr); return {ctx, w, h};
}
// x 軸は「tick index」（時間軸だと週末ギャップ・夜間で潰れるため）。ラベルに実時刻を出す。
function drawChart(id, seriesList, opts = {}){
  const cv = document.getElementById(id), {ctx, w, h} = setupCanvas(cv);
  const pad = {l: 74, r: 14, t: 12, b: 26};
  const n = S.ts.length;
  let ymin = Infinity, ymax = -Infinity;
  seriesList.forEach(sr => sr.data.forEach(v => { if (v != null) { if (v < ymin) ymin = v; if (v > ymax) ymax = v; } }));
  (opts.hlines || []).forEach(hl => { if (hl.v < ymin) ymin = hl.v; if (hl.v > ymax) ymax = hl.v; });
  const mgn = Math.max((ymax - ymin) * 0.08, 1e-9); ymin -= mgn; ymax += mgn;
  const X = i => pad.l + (w - pad.l - pad.r) * (n <= 1 ? 0 : i / (n - 1));
  const Y = v => pad.t + (h - pad.t - pad.b) * (1 - (v - ymin) / (ymax - ymin));
  ctx.clearRect(0, 0, w, h);
  ctx.font = '10px ' + CSS.getPropertyValue('--mono');
  // grid + y labels
  for (let k = 0; k <= 4; k++) {
    const v = ymax - (ymax - ymin) * k / 4, y = Y(v);
    ctx.strokeStyle = col('grid'); ctx.lineWidth = 1;
    ctx.beginPath(); ctx.moveTo(pad.l, y); ctx.lineTo(w - pad.r, y); ctx.stroke();
    ctx.fillStyle = col('muted'); ctx.textAlign = 'right';
    ctx.fillText(opts.fmt ? opts.fmt(v) : Math.round(v).toLocaleString(), pad.l - 6, y + 3);
  }
  // x labels（実時刻・5 分割）＋ 日境界の縦グリッド
  ctx.textAlign = 'center';
  for (let k = 0; k <= 5; k++) {
    const i = Math.round((n - 1) * k / 5);
    ctx.fillStyle = col('muted'); ctx.fillText(fmtT(S.ts[i]), X(i), h - 6);
  }
  for (let i = 1; i < n; i++) {
    if (new Date(S.ts[i]).getUTCDate() !== new Date(S.ts[i-1]).getUTCDate()) {
      ctx.strokeStyle = col('grid'); ctx.setLineDash([2,4]);
      ctx.beginPath(); ctx.moveTo(X(i), pad.t); ctx.lineTo(X(i), h - pad.b); ctx.stroke();
      ctx.setLineDash([]);
    }
  }
  // hlines
  (opts.hlines || []).forEach(hl => {
    ctx.strokeStyle = hl.c; ctx.lineWidth = 1.5; ctx.setLineDash(hl.dash || [5,4]);
    const y = Y(hl.v);
    ctx.beginPath(); ctx.moveTo(pad.l, y); ctx.lineTo(w - pad.r, y); ctx.stroke(); ctx.setLineDash([]);
    ctx.fillStyle = hl.c; ctx.textAlign = 'left';
    ctx.fillText(hl.label, pad.l + 4, y - 4);
  });
  // series
  seriesList.forEach(sr => {
    ctx.strokeStyle = sr.c; ctx.lineWidth = sr.lw || 1.6; ctx.beginPath();
    let started = false;
    for (let i = 0; i < n; i++) {
      const v = sr.data[i]; if (v == null) { started = false; continue; }
      const x = X(i), y = Y(v);
      if (started) ctx.lineTo(x, y); else { ctx.moveTo(x, y); started = true; }
    }
    ctx.stroke();
  });
  // event verticals
  if (opts.events) EV.forEach(e => {
    let idx = 0; while (idx < n - 1 && S.ts[idx] < e.ts) idx++;
    const isEntry = e.kind === 'entry';
    ctx.strokeStyle = isEntry ? col('entry') : (e.kind === 'tp' ? col('tp') : col('danger'));
    ctx.lineWidth = 1.2; if (!isEntry) ctx.setLineDash([4,3]);
    ctx.beginPath(); ctx.moveTo(X(idx), pad.t); ctx.lineTo(X(idx), h - pad.b); ctx.stroke();
    ctx.setLineDash([]);
    ctx.fillStyle = ctx.strokeStyle; ctx.textAlign = 'center';
    ctx.fillText(e.kind, X(idx), pad.t + 8);
  });
}

// ---- chart 1: price + levels ----
(function(){
  const plan = META.plan;
  const hl = [];
  if (plan.stop_price != null) hl.push({v: plan.stop_price, c: col('danger'), label: '損切り ' + plan.stop_price});
  if (plan.tp_price != null) hl.push({v: plan.tp_price, c: col('tp'), label: '利確 ' + plan.tp_price});
  (plan.entries || []).forEach((e, i) => { if (e.price != null) hl.push({v: e.price, c: col('muted'), dash: [2,3], label: '#' + (i+1) + ' 指値 ' + e.price}); });
  document.getElementById('lg1').innerHTML =
    '<span><i style="background:'+col('price')+'"></i>bid</span>' +
    '<span><i style="background:'+col('danger')+'"></i>損切り</span>' +
    '<span><i style="background:'+col('tp')+'"></i>利確</span>' +
    '<span><i style="background:'+col('muted')+'"></i>指値</span>';
  drawChart('c_price', [{data: S.bid, c: col('price')}], {hlines: hl, events: true});
})();
// ---- chart 2: balance & equity ----
drawChart('c_equity', [
  {data: S.balance, c: col('balance')},
  {data: S.equity, c: col('equity'), lw: 2},
], {fmt: fmtY, events: true});
// ---- chart 3: required margin ----
drawChart('c_margin', [{data: S.required_margin, c: col('margin'), lw: 1.8}], {fmt: fmtY});
// ---- chart 4: margin ratio ----
drawChart('c_ratio', [{data: S.margin_ratio.map(r => r == null ? null : r * 100), c: col('ratio'), lw: 2}],
  {hlines: [{v: 100, c: col('danger'), label: 'ロスカット 100%'}], fmt: v => v.toFixed(0) + '%', events: true});

// ---- events table ----
document.getElementById('evrows').innerHTML = EV.map(e =>
  `<tr><td>${new Date(e.ts).toISOString().slice(0,19).replace('T',' ')}</td><td>${e.kind}${e.note?'（'+e.note+'）':''}</td>` +
  `<td>${e.price.toFixed(1)}</td><td>${e.units}</td>` +
  `<td style="color:${e.pnl>0?'var(--safe)':e.pnl<0?'var(--danger)':'var(--muted)'}">${e.pnl?((e.pnl>0?'+':'')+fmtY(e.pnl)):'—'}</td>` +
  `<td>${e.kind==='entry'?(e.note||''):''}</td></tr>`).join('');
</script>
</body>
</html>
"""


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--in", dest="src", required=True, type=Path)
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--title", default=None)
    args = ap.parse_args()

    data = json.loads(args.src.read_text(encoding="utf-8"))
    title = args.title or args.src.stem
    html = _PAGE.replace("__TITLE__", title).replace("__DATA__", json.dumps(data, ensure_ascii=False))
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(html, encoding="utf-8")
    print(f"レポート生成: {args.out}（{args.out.stat().st_size:,} bytes）")


if __name__ == "__main__":
    main()
