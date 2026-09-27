"""ジョブ結果 → report.json（adapter 層・Phase 4 F-8）。

`run_backtest` の結果（1 run ＝ 1 区間）を report_ui の**既存 UC / Presenter へ渡して**
`report.json` を書く。写像の式（trades 16 キー・summary・report ラベル）は 1 行も写さない
（複製禁止）。ここが持つのは「ジョブ仕様 → UC の引数」への変換だけである。

    spec.json ＋ BacktestResult ──> BuildReportPayload.execute_single ──> ReportUiPresenter
                                    （report_ui の単一ソース）

**単一区間である**ことの扱い（不実データの回避）:
  - segments / summary のキーは "single"。"is" を名乗らせない（実施していない区分の捏造）。
  - degradation は空・verdict は `not_evaluated`（UC 側の構造的な歯止め）。
  - `ReportMeta` の**既定値は使わない**。既定は StopEntryProbe 実験の所与
    （``split="2026-04-15"`` / ``note="IS/OOS 単純分割…"`` / ``params="ProbeDir=2…"``）であり、
    別の実験で行われた分割の事実である。単一 run にそのまま載せると、実施していない
    分割を報告することになる。よって本 writer が job の事実だけで組み直す。

時刻の int 化: UC は int 時刻のみを受ける契約で、int 化は**呼び出し側の Composition Root
が担う**（`build_report_payload` docstring）。bars / trades の時刻は供給源によって
UNIX 秒・Timestamp・datetime64 のいずれにもなる。その吸収は report_ui の
`tools/int_time_views` が単一ソースで持つので、ここには 1 行も書かない（H-D1/H-D4）。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

from simulator.report_ui.adapter.report_presenter import ReportUiPresenter
from simulator.report_ui.usecase.build_report_payload import BuildReportPayload
# int 時刻ビューは report_ui の単一ソースを使う（H-D1/H-D4）。ここへ写すと、同じ
#   payload を作る 2 経路（IS/OOS 実 run と sim ジョブ）が静かに食い違う。
from simulator.report_ui.tools.int_time_views import (
    IntTimeBar,
    ResultView,
)
from simulator.report_ui.usecase.report_meta import ReportMeta

#: 結果ペイロードのファイル名（sim core の `/data/{job_id}/{file}` が配信する名前）。
REPORT_FILENAME = "report.json"
#: 単一区間の segments / summary キー（"is" は区分の捏造になるため使わない）。
SINGLE_SEGMENT_KEY = "single"
#: payload に残す単一区間の注記（比較が未実施であることを payload 自身に持たせる）。
SINGLE_SEGMENT_NOTE = (
    "本レポートは単一区間（1 run）。IS/OOS 分割・劣化比較・合否判定は未実施。"
)


def _run_facts(spec: dict, backtest: dict, bars: list) -> dict:
    """サマリーの run の事実（通貨・レバレッジ・銘柄数・バー数）を job の値から拾う。

    無い値は入れない（捏造しない）。
    - 通貨: `.ini` の `Currency`（settings 経路のみ存在）。
    - レバレッジ: `backtest.leverage`（エンジンへ渡った値）。
    - 銘柄数: 1。エンジンは 1 run に 1 銘柄だけを取る（`build_interactor` の `symbol`）。
    - バー数: 表示用の足の本数。sim の経路は取引開始の境界を持たない
      （`simulator/main/tester_settings/window.py` が `trading_start` を常に None にする）ので、
      足はすべてテスト期間の足である。MT5 の Bars と同じ数え方（取引開始以降の足の本数）は
      report_ui export の IS 区間で MT5 xlsx と一致（13206・2026-09-27）。
    """
    facts: dict = {"symbols": 1, "tested_bars": len(bars)}
    currency = ((spec.get("settings") or {}).get("tester") or {}).get("Currency")
    if currency:
        facts["currency"] = currency
    if backtest.get("leverage") is not None:
        facts["leverage"] = backtest["leverage"]
    return facts


def write(
    job_dir: Any,
    result: Any,
    *,
    load_run_inputs: "Callable[[dict], tuple[Any, Any]]",
    contacts_supply: "Callable[[list, dict], list] | None" = None,
) -> Path:
    """`job_dir` へ `report.json` を書き、そのパスを返す。

    ``result``: `run_backtest` が返した `BacktestResult`（成功 run のみ渡すこと）。
    ``load_run_inputs``: (bars, symbol_spec) の供給（**必須**）。`BacktestResult` は bars を
      保持しないため、表示用のローソク足と建値推定（MFE/MAE）に要る bars を取り直す口。
      実体（EA 別 MarketDataPort の選択・CSV 解析）は `simulator.main` の単一ソースにあり、
      その束縛は **Composition Root（`main/run_job.py`）が持つ**（R-4）。adapter が
      `simulator.main` を既定値として掴むと依存が外向き（adapter→main）になる。
    ``contacts_supply``: (bars, backtest) → `agg.contacts`（`[{time, price, dir}]`）の供給。
      未指定なら `agg` に `contacts` キーを生やさない（Phase 4 までの payload と等価）。
      ここへ渡す ``bars`` は**読み込み済みの int 時刻ビュー**である。もう一度読ませると、
      表示している足と接点を算出した足が別物になり得る。
    """
    job_dir = Path(job_dir)
    spec = json.loads((job_dir / "spec.json").read_text(encoding="utf-8"))
    backtest = dict(spec.get("backtest") or {})

    raw_bars, symbol_spec = load_run_inputs(backtest)
    bars = [IntTimeBar(b) for b in raw_bars]
    contacts = contacts_supply(bars, backtest) if contacts_supply is not None else None

    # SL/TP は job 仕様の値のみ。未指定は 0 ＝ UC 側で空文字になる（価格を捏造しない）。
    ea_params = {
        "sl_points": backtest.get("stop_loss_points") or 0,
        "tp_points": backtest.get("take_profit_points") or 0,
    }
    symbol = backtest.get("symbol", "")
    timeframe = backtest.get("period", "")
    strategy = backtest.get("ea_name", "")

    payload = BuildReportPayload().execute_single(
        result=ResultView(result),
        bars=bars,
        spec=symbol_spec,
        ea_params=ea_params,
        # 接点マーカー（FR-18）。供給が無ければ None＝agg に contacts キーを生やさない。
        contacts=contacts,
        meta={
            "symbol": symbol,
            "timeframe": timeframe,
            "strategy": strategy,
            # 期間の表示文字列は job 仕様に無い（実測していないものを書かない）。
            "period": "",
            "label": "",
            **_run_facts(spec, backtest, bars),
        },
        # 別実験の所与（既定値）を持ち込まない。job の事実だけで組む。
        report_meta=ReportMeta(
            expert=strategy,
            symbol=symbol,
            timeframe=timeframe,
            params="",
            split="",
            note=SINGLE_SEGMENT_NOTE,
        ),
        segment_key=SINGLE_SEGMENT_KEY,
        contract_notes_extra=[SINGLE_SEGMENT_NOTE],
    )

    out = job_dir / REPORT_FILENAME
    ReportUiPresenter().present_report_payload(payload, out)
    return out
