"""tools.pipeline_runner — 段階パイプライン実行器の**単一実装**（ISSUE-502 D-17）。

台帳（.doc/solid_audit_20260906.md の D-17）が実測した状態: 段階選択（``select_stages``）と
実行ループ（``run_pipeline``: 開始ログ → 実行 → 例外捕捉 → 経過計測 → 終了ログ → 失敗時中断
→ サマリ）が ``tools/acquire_marketdata.py`` と ``tools/build_tick_rollup.py`` に
**docstring まで一字一句同一**で二重に存在していた。ログ書式・中断規則・戻り値規約を変えるには
2 箇所を同時に直す必要があり、片方だけの編集が無言で通る状態だった。

責務（SRP）: 本モジュールは「与えられた段階名を順に実行し、結果を記録して集約する」だけを持つ。
段階が何をするか・文脈（PipelineContext）が何を持つかは知らない。文脈は呼び出し側が閉じ込めた
:paramref:`run_stage` の内側にあり、本モジュールの引数に現れない（DIP: 実行器は具体の文脈型に
依存しない）。ログ出力先も呼び出し側が注入する（CLI ごとのロガー名を保つため）。

なぜ ``tools`` に置くか: 段階名の並びとログでユーザーへ進捗を伝える規約は CLI の関心であり、
ライブラリ（marketdata / simulator）の関心ではない。``tools/__init__.py`` の「ロジックの重複を
持たない合成点」宣言は**重複の禁止**であって、CLI 共通の実行器を持つことの禁止ではない。
第 2 実装が復活していないことは ``tools/tests/test_pipeline_runner_single_implementation.py`` が
走査で強制する。
"""
from __future__ import annotations

import logging
import time
from typing import Callable, List, Optional, Sequence


def select_stages(
    stage_names: Sequence[str], skip: Sequence[str], only: Optional[str]
) -> List[str]:
    """実行する段階名を実行順で返す。``only`` 指定時は ``skip`` を無視し単一段階のみ。"""
    if only is not None:
        return [only]
    skip_set = set(skip)
    return [s for s in stage_names if s not in skip_set]


def run_pipeline(
    stages: Sequence[str],
    *,
    run_stage: Callable[[str], int],
    log: logging.Logger,
    continue_on_error: bool = False,
) -> int:
    """段階を順次実行し、全段成功なら 0・いずれか失敗なら 1 を返す。

    Args:
        stages: 実行する段階名（実行順）。
        run_stage: 段階名を受け取り終了コード（0=成功）を返す実体。文脈の受け渡しは
            呼び出し側が閉包で閉じ込める（実行器は文脈の形を知らない）。
        log: 進捗・サマリの出力先（CLI ごとのロガーを注入する）。
        continue_on_error: True なら失敗段があっても後続を実行する（戻り値は 1 のまま）。

    段階が送出した例外は捕捉して当該段の失敗（rc=1）として扱い、サマリへ型と文言を残す
    （1 段の失敗でパイプライン全体の記録を失わせない）。
    """
    results: "List[tuple]" = []
    overall_ok = True
    for stage in stages:
        log.info("=== stage %s 開始 ===", stage)
        t0 = time.monotonic()
        rc, err = 1, None
        try:
            rc = run_stage(stage)
        except Exception as exc:  # noqa: BLE001 — 段階例外を集約しサマリに反映
            err = exc
            rc = 1
            log.error("stage %s 例外: %s: %s", stage, type(exc).__name__, exc)
        elapsed = time.monotonic() - t0
        ok = rc == 0 and err is None
        results.append((stage, rc, elapsed, err))
        log.info("=== stage %s 終了 rc=%s elapsed=%.2fs %s ===",
                 stage, rc, elapsed, "OK" if ok else "NG")
        if not ok:
            overall_ok = False
            if not continue_on_error:
                break
    log.info("---- サマリ ----")
    for stage, rc, elapsed, err in results:
        status = "OK" if (rc == 0 and err is None) else "NG"
        detail = f" ({type(err).__name__}: {err})" if err is not None else ""
        log.info("  %-7s %s rc=%s %.2fs%s", stage, status, rc, elapsed, detail)
    return 0 if overall_ok else 1


__all__ = ["select_stages", "run_pipeline"]
