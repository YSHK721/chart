"""シミュレーション結果（子文書）の実測検定（Playwright）。

Phase 4 では 3 窓チャート（ローソク足 / Balance / Drawdown）のパリティ 12 点を移植元
report_ui と突き合わせていた。3 窓チャートは撤去済み（2026-09-27 依頼者指示・チャートは
親ページの売買履歴チャートだけが持つ）ため、本検定は次の 3 つを実測で固定する。

    1. 残るパリティ点（移植元 report_ui と同値であるべきもの）
         A1  ヘッダ #topbar 規則の当たり方（display/padding/h1・hSel が同一行）
         S1  取引履歴 12 列（順序・キー・ラベル・行数）
         S3  行 hover → 該当行 .hl ＋ hSel 連動ラベル（table.js / linkage の流用）
    2. 撤去の実測（R1）: チャートの器（#chartWrap / #price-chart / #paneBal / #paneDD /
         #chartBadge / #toggleContacts）と、比較タブ外の canvas が子文書に**無い**
    3. 計算量（R2・無駄の不在）: チャート材料（lightweight-charts / chart_overlay /
         candles）へのリクエスト発行が 0。**取引数を増やしても発行リクエスト数が増えない**
         （2 点でオーダーを固定する。回数そのものは期待値に焼き込まない）。
         「作ってから捨てる」欠陥は出力が正しいままなので状態検証では落ちない——
         発行そのものを観測境界（ブラウザの resource timing＝公開面）で数える。

fixture は移植元 `report_ui/tests/e2e/verify_parity.py` の 2 区間ペイロードを import する
（sim 用に別のダミーを作らない＝ずれの余地を残さない）。開くのは**製品そのもの**の
子文書 `/sim/report_view.html` である（裁定 B）。

chromium / playwright 不在環境では skip（移植元 verify.py の規約準拠）。
"""
from __future__ import annotations

import copy
import json
import shutil
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[4]
# 移植元 e2e は `from e2e import _harness` を使う。その解決点を明示的に載せる
# （pytest の rootdir 挿入に依存すると、単体起動と pytest 起動で挙動が変わる）。
_REPORT_TESTS = _REPO / "simulator" / "report_ui" / "tests"
if str(_REPORT_TESTS) not in sys.path:
    sys.path.insert(0, str(_REPORT_TESTS))

from e2e import _harness  # noqa: E402
from e2e import verify_parity  # noqa: E402  (2 区間 fixture の唯一源)

pytestmark = pytest.mark.e2e

REPORT_WEB = _REPO / "simulator" / "report_ui" / "web"
SIM_WEB = _REPO / "simulator" / "sim_ui" / "web"

JOB_ID = "parityjob"
#: R2 の 2 点目（取引数を増やした入力）。増やしても発行リクエスト数が増えないことを固定する。
JOB_ID_SCALED = "parityjob3"
_SCALE = 3


def _payload_scaled(k: int) -> dict:
    """2 区間 payload の trades を k 倍にしたもの（R2 の「入力を増やす」side）。

    id と時刻をずらしたクローンで増やす。集計（agg 等）は据え置き——本検定で増やした入力を
    読むのは取引明細（行数）とリクエスト数の突合だけである。
    """
    payload = copy.deepcopy(verify_parity._payload())
    for seg in payload["segments"].values():
        base_trades = list(seg["trades"])
        for j in range(1, k):
            for t in base_trades:
                clone = dict(t)
                clone["id"] = t["id"] + 1000 * j
                clone["order"] = clone["id"]
                clone["entry_time"] = t["entry_time"] + 3600 * j
                clone["exit_time"] = t["exit_time"] + 3600 * j
                seg["trades"].append(clone)
    return payload


def _build_sim_web_root(tmp_path: Path) -> Path:
    """sim 表示層の配信面（/sim/report-js・/sim/report-css・/sim/js・/sim/data）を再現する。

    実運用の経路は `composition_root_display.build_sim_display_app` の prefix ルートだが、
    ここでは静的ハーネスで同じ**URL 構造**を用意する（front の絶対パス import は URL に
    しか依存しない）。配信面そのものの検定は `tests/integration/test_serve_sim_display.py`。

    lightweight-charts の vendor は**置かない**——子文書はもう読まない（R2）。置かずに
    ページが組み上がることが、依存が消えたことの直接証拠になる。

    開くのは**製品そのもの**の子文書 `/sim/report_view.html` である（裁定 B）。検定用の
    ページを別に書かない——書けば「fixture では動くが製品では動かない」を作れてしまう。
    """
    root = tmp_path / "simweb"
    sim = root / "sim"
    (sim / "js" / "adapter").mkdir(parents=True)
    shutil.copytree(REPORT_WEB / "js", sim / "report-js")
    shutil.copytree(REPORT_WEB / "css", sim / "report-css")
    shutil.copytree(SIM_WEB / "js" / "adapter" / "front", sim / "js" / "adapter" / "front")
    # 親の売買履歴チャート用モデル（sim_result_chart_view が import する）。子文書の module
    #   graph に含まれるため、配信面に無いと合成根ごと読み込みが落ちる。
    shutil.copytree(SIM_WEB / "js" / "usecase", sim / "js" / "usecase")
    shutil.copytree(SIM_WEB / "css", sim / "css")
    shutil.copy(SIM_WEB / "report_view.html", sim / "report_view.html")
    # Chart.js（比較グラフ用・移植元 vendor 無改変）は子文書が読む＝使う読込なので置く。
    (sim / "report-vendor").mkdir()
    shutil.copy(REPORT_WEB / "vendor" / "chart.umd.js", sim / "report-vendor" / "chart.umd.js")
    for job_id, payload in (
        (JOB_ID, verify_parity._payload()),
        (JOB_ID_SCALED, _payload_scaled(_SCALE)),
    ):
        data_dir = sim / "data" / job_id
        data_dir.mkdir(parents=True)
        (data_dir / "report.json").write_text(
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
            encoding="utf-8",
        )
    return root


# --- 観測（残るパリティ点は両画面で同じ問いを投げる）------------------------------

_COLUMNS = """() => ({
  keys: [...document.querySelectorAll('#tradeTable thead th')].map((t) => t.dataset.k),
  labels: [...document.querySelectorAll('#tradeTable thead th')].map((t) => t.textContent),
  rows: document.querySelectorAll('#tradeTable tbody tr').length,
})"""

_HL_ROWS = """() => [...document.querySelectorAll('#tradeTable tbody tr.hl')].map((r) => +r.dataset.id)"""

# ヘッダは移植元 style.css:26-35 の `#topbar` / `#topbar h1` を**実体のまま**使う。
# id を別名にすると id セレクタが 1 つも当たらず、見た目だけが静かにずれる
# （実測差分: display block／h1 24px／hSel が 2 行目へ落ちる／ヘッダ高 66px）。
_HEADER_STYLE = """() => {
  const h = document.querySelector('#topbar');
  if (!h) return null;
  const cs = getComputedStyle(h);
  const h1 = h.querySelector('h1');
  const h1cs = h1 ? getComputedStyle(h1) : null;
  return {
    display: cs.display, padding: cs.padding, alignItems: cs.alignItems, gap: cs.gap,
    height: Math.round(h.getBoundingClientRect().height),
    h1FontSize: h1cs ? h1cs.fontSize : null,
    h1Margin: h1cs ? h1cs.margin : null,
    sameRow: (() => {
      const s = document.querySelector('#hSel');
      if (!s || !h1) return null;
      const a = s.getBoundingClientRect(), b = h1.getBoundingClientRect();
      return a.top < b.bottom && b.top < a.bottom;
    })(),
  };
}"""

# R1: チャートの器と canvas の不在（比較タブ＝Chart.js の canvas だけを許す）。
_CHART_ABSENCE = """() => ({
  receptacles: ['#chartWrap', '#price-chart', '#paneBal', '#paneDD', '#chartBadge', '#toggleContacts']
    .filter((s) => document.querySelector(s)),
  canvasesOutsideCompare: [...document.querySelectorAll('canvas')]
    .filter((c) => !c.closest('.mv-pane[data-pane="compare"]')).length,
})"""

# R2: 発行したリクエスト（観測境界＝ブラウザの resource timing・公開面）。
#   ドキュメント自身は含まれない。名前は URL（クエリ含む）。
_RESOURCES = """() => performance.getEntriesByType('resource').map((e) => e.name)"""


def _report_ui_page(tmp_path: Path):
    """移植元 report_ui の画面（同一 payload）を立てる。"""
    p, browser, page, httpd = _harness.launch(verify_parity._build_web_root, tmp_path / "ref")
    return p, browser, page, httpd


def _open_sim(browser, port: int, job_id: str):
    page = browser.new_page(viewport={"width": 1600, "height": 1000})
    errors: list = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(f"http://127.0.0.1:{port}/sim/report_view.html?job={job_id}")
    page.wait_for_function("window.__simReportViewReady === true", timeout=15000)
    page.wait_for_function(
        "() => document.querySelectorAll('#tradeTable tbody tr').length > 0", timeout=15000
    )
    return page, errors


def test_sim_display_without_chart(tmp_path: Path) -> None:
    """残るパリティ点（A1/S1/S3）＋撤去の実測（R1）＋計算量（R2）を 1 セッションで固定する。"""
    p, browser, ref, ref_httpd = _report_ui_page(tmp_path)
    sim = sim_httpd = sim_scaled = None
    sim_errors: list = []
    try:
        ref.set_viewport_size({"width": 1600, "height": 1000})
        root = _build_sim_web_root(tmp_path / "sim")
        port = _harness.free_port()
        sim_httpd = _harness.serve(str(root), port)
        sim, sim_errors = _open_sim(browser, port, JOB_ID)

        # A1: ヘッダ #topbar 規則が両画面で同じく当たっている（見た目のパリティ）。
        ref_head = ref.evaluate(_HEADER_STYLE)
        sim_head = sim.evaluate(_HEADER_STYLE)
        assert ref_head is not None and sim_head is not None, "ヘッダ #topbar が無い"
        assert ref_head["display"] == "flex", f"移植元のヘッダ規則が当たっていない: {ref_head}"
        for key in ("display", "padding", "alignItems", "gap", "h1FontSize", "h1Margin"):
            assert ref_head[key] == sim_head[key], (
                f"ヘッダ {key}: {ref_head[key]!r} vs {sim_head[key]!r}（#topbar 規則の当たり方が違う）"
            )
        assert sim_head["sameRow"] is True, "sim で hSel が 2 行目へ落ちている（#topbar の flex 未適用）"
        assert sim_head["height"] <= ref_head["height"], (
            f"ヘッダ高: sim {sim_head['height']} > 移植元 {ref_head['height']}"
            "（#topbar 規則が当たっていない疑い）"
        )

        # S1: 取引履歴 12 列（順序・キー・ラベル）＋ 行数。
        ref_cols = ref.evaluate(_COLUMNS)
        sim_cols = sim.evaluate(_COLUMNS)
        assert len(ref_cols["keys"]) == 12, f"点S1 列数: {ref_cols['keys']}"
        assert ref_cols == sim_cols, f"点S1 明細 12 列: {ref_cols} vs {sim_cols}"

        # S3: 行 hover（table→linkage）→ 該当行 .hl ＋ hSel 連動ラベル。
        #   移植元は明細をタブの裏に置く（既定は「比較・判定」）ので、実 hover の前にタブを開く。
        ref.click('.mv-tab[data-tab="detail"]')
        ref.wait_for_timeout(150)
        ref.hover('#tradeTable tbody tr[data-id="2"]')
        sim.hover('#tradeTable tbody tr[data-id="2"]')
        ref.wait_for_timeout(200)
        sim.wait_for_timeout(200)
        assert ref.evaluate(_HL_ROWS) == [2], "点S3 移植元の行 hover が効かない"
        assert ref.evaluate(_HL_ROWS) == sim.evaluate(_HL_ROWS), "点S3 行 hover→強調"
        assert ref.inner_text("#hSel") == sim.inner_text("#hSel"), "点S3 行 hover のラベル"

        # R1: チャートの器と canvas の不在（撤去の実測・2026-09-27 依頼者指示）。
        absence = sim.evaluate(_CHART_ABSENCE)
        assert absence["receptacles"] == [], f"R1 チャートの器が残っています: {absence}"
        assert absence["canvasesOutsideCompare"] == 0, (
            f"R1 比較タブ外に canvas があります（チャートの第 2 実装の疑い）: {absence}"
        )

        # R2a: チャート材料への発行が 0（発行した計算 − 出力に使った計算 = 0 の資材面）。
        resources = sim.evaluate(_RESOURCES)
        wasted = [r for r in resources
                  if "lightweight-charts" in r or "chart_overlay" in r or "/candles" in r]
        assert wasted == [], f"R2 チャート材料を読んでいます（描く先が無い＝浪費）: {wasted}"

        # R2b: 入力（取引数）を {SCALE} 倍にしても発行リクエスト数が増えない（オーダーの表明）。
        #   回数そのものは焼き込まない——固定するのは「入力に比例して発行が増えない」こと。
        sim_scaled, scaled_errors = _open_sim(browser, port, JOB_ID_SCALED)
        rows_base = sim.evaluate("() => document.querySelectorAll('#tradeTable tbody tr').length")
        rows_scaled = sim_scaled.evaluate(
            "() => document.querySelectorAll('#tradeTable tbody tr').length")
        assert rows_scaled > rows_base, (
            f"R2 の前提が破れている（入力が実際に増えていない）: {rows_base} → {rows_scaled}"
        )
        n_base = len(resources)
        n_scaled = len(sim_scaled.evaluate(_RESOURCES))
        assert n_scaled <= n_base, (
            f"R2 取引数 {_SCALE} 倍で発行リクエストが増えました: {n_base} → {n_scaled}"
        )
        assert scaled_errors == [], f"scaled 画面の JS エラー: {scaled_errors}"

        # 全体: sim 画面で JS エラーが出ていない（lwc 不在の配信面でも 0＝依存が消えた証拠）。
        assert sim_errors == [], f"sim 画面の JS エラー: {sim_errors}"
    finally:
        if sim_scaled is not None:
            sim_scaled.close()
        if sim is not None:
            sim.close()
        if sim_httpd is not None:
            sim_httpd.shutdown()
        browser.close()
        ref_httpd.shutdown()
        p.stop()
