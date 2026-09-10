"""ジョブ成果物としての実行トレース（adapter 層・RUN_TRACE_BASIC_DESIGN §6.5）。

アクター（改訂の動機）: **`job_dir` の中の置き場**。何を残すか（`adapter/trace/*`）とも、
どう永続化するか（`parquet_trace_store`）とも別の理由で変わる。

出力は `job_dir` **直下の平坦名**である（是正 F-3・実測に基づく）:
    `FileJobLedger._FILENAME_RE = \\A[A-Za-z0-9_-]+\\.[A-Za-z0-9]+\\Z`
    （`file_job_ledger.py:41`）は区切りを含む名前を受理せず、配信口
    `serve_sim_jobs.py:180-190` は `_data, job_id, filename` の**3 セグメント固定**である。
    サブディレクトリ（初版の `job_dir/trace/`）に置けば「書けたが誰も読めない」成果物になる。

**公開規則は所有しない**（是正 F-3）: 公開可否の関門は台帳（`simulator/sim_ui/adapter/file_job_ledger.py`）と配信口が既に
持っており、ここが持つのは**置き場（ファイル名）だけ**である。`job_id` も導出しない
——`run_job.py`（Composition Root）が `job_dir.name` を渡す（`FileJobLedger.job_dir` の
規約の 2 つ目の実装を作らない）。

指標 registry は **Callable で注入**する（是正 D-4）:
    「その run が使った指標 registry」の公開到達点はエンジン（`simulator/usecase/run_backtest.py`）に無く、
    私有属性（_indicators）へ手を伸ばすのは ISSUE-395/398・ISSUE-405 で 2 度是正済みの
    カプセル化破りと同型である。また `sim_ui/**`（`main/` 以外）は `simulator.main` を
    import できない（層ゲート `test_sim_ui_import_direction.py`）。よって
    `run_job.py` が `build_ea_indicators(**backtest)` で組んで注入する
    （先例 `run_job.py:219-224` の _supply_contacts）。

trace_meta.json に**列の意味を書き写さない**（D-3b）: 各 trace モジュールが公開する
宣言（`COLUMNS` / `columns_for` の戻り）を読むだけにする。書き写した時点で 2 箇所化する。

`marketdata_window` の有無を残す理由（§6.5.2 の既知の不整合を隠さない）:
    指標 registry は data_path の**全 CSV** から作られる一方、bars は
    `marketdata_window` で絞られる。したがって窓付き run では bar_index の指す先が
    両者で一致しない。これはエンジンに既存の性質であり（別 ISSUE）、段階 3 が負う義務は
    **隠さないこと**だけである——段階 4 の分析面が誤読しないよう payload 自身に載せる。

書出し失敗の扱いは呼出側（`run_job.py`）が持つ: run 自体は成功しているので終了コードは
変えず、理由を trace_error.json へ残す（先例 _record_report_payload_error）。
ここでは握らずそのまま送出する——握って握りつぶすと、書けなかったことが誰にも届かない。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

from simulator.adapter.trace import columnar_run_trace, indicator_trace, parquet_trace_store

#: 点粒度の記録（1 評価点 1 行）。
POINTS_FILENAME = "trace_points.parquet"
#: 足粒度の記録（bar_index ＋ エンジンが実際に読んだ指標値）。
INDICATORS_FILENAME = "trace_indicators.parquet"
#: 窓・記録行数・列の宣言・`marketdata_window` の有無・生成元 job_id。
META_FILENAME = "trace_meta.json"


def write(
    job_dir: Any,
    trace: Any,
    *,
    job_id: str,
    indicators_supply: "Callable[[], Any]",
    marketdata_window: Any = None,
) -> "dict[str, Any]":
    """トレース 3 本を `job_dir` 直下へ書き、書いた事実を返す。

    ``trace``: run 中に記録した `ColumnarRunTrace`（run 完了後に渡すこと・§12.7 は
      実行中の部分結果を公開しない）。
    ``job_id``: 生成元のジョブ識別子。**呼出側が渡す**（ここで `job_dir.name` を
      読み直すと台帳の規約の 2 つ目の実装ができる）。
    ``indicators_supply``: その run が使った指標 registry を返す注入 Callable
      （`names()` と `get(name)` を持つ実体）。**1 回だけ**呼ぶ。
    ``marketdata_window``: run に指定された取得窓（無指定は `None`）。値そのものは
      使わず、**有無だけ**を meta へ残す（§6.5.2）。
    """
    job_dir = Path(job_dir)
    job_dir.mkdir(parents=True, exist_ok=True)

    point_columns = trace.columns
    points_rows = parquet_trace_store.write_columns(
        job_dir / POINTS_FILENAME, point_columns
    )

    # 指標 registry の供給は 1 回だけ発行する（行ごと・系列ごとに引き直すと、出力は
    #   同じまま registry の再構築が発行数ぶん起きる＝作ってから捨てる形）。
    indicator_columns = indicator_trace.IndicatorTrace(indicators_supply()).columns_for(
        trace.bar_indices()
    )
    indicators_rows = parquet_trace_store.write_columns(
        job_dir / INDICATORS_FILENAME, indicator_columns
    )

    start, end = trace.window_bounds
    windowed = marketdata_window is not None
    meta = {
        "job_id": job_id,
        "window": {"start": start, "end": end},
        "rows": {"points": points_rows, "indicators": indicators_rows},
        # 列の意味は各モジュールの宣言を**読むだけ**（ここに列名を書かない・D-3b）。
        "columns": {
            "points": list(columnar_run_trace.COLUMNS),
            "indicators": list(indicator_columns),
        },
        "primary_key": {
            "points": list(columnar_run_trace.PRIMARY_KEY),
            "indicators": [indicator_trace.BAR_INDEX_COLUMN],
        },
        # §6.5.2: 窓付き run では指標側と点側の bar_index が同じ足を指さない。
        #   隠さずに載せ、段階 4 の分析面が「正しい対応づけ」として提示しないようにする。
        "marketdata_window": windowed,
        "indicator_bar_index_is_comparable": not windowed,
    }
    (job_dir / META_FILENAME).write_text(
        json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    return {
        "rows": meta["rows"],
        "files": [POINTS_FILENAME, INDICATORS_FILENAME, META_FILENAME],
    }
