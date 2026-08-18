"""Settings から 1 run を実行する facade（内部設計 §6 実行 A・A-1 で経路を一本化）。

1. 層名/責務:
    main 層（Composition Root）。`TesterSettings` と実行資源の注入束を受け、
    `effective_to_interactor_kwargs` → `build_interactor` → 窓の事後検証 → 実行、へ
    結線する。非対象判定は `unsupported`、写像は `kwargs_mapper`、窓は `window` が所有し、
    本モジュールは**結線だけ**を持つ（SRP）。終了コードの語彙は `exit_codes` が
    唯一の宣言場所であり、本モジュールはそれを import して使う。

    A-1（ISSUE-397）で `MATH_CALCULATIONS` 専用の追加経路（旧 §8.2）を撤去した。
    `Math calculations` も同じ 1 本の実行段を通る。分岐は 1 つも持たない——バー系列を
    消費するか否かは `TickModelSpec.requires_market_data` が宣言し、`build_interactor`
    が構成の選択で実現する（本モジュールは `tick_model` を見ない）。

2. 含む構造:
    run_from_settings（実行 A）: 設定 ＋ 注入束 → (終了コード, 結果, 実行メタ)。
    run_effective_settings      : 実行段（例外を送出する。終了コード翻訳を行わない）。

3. 元 MQL 対応:
    MT5 ストラテジーテスターの Start ボタン（Settings タブの内容で 1 パスを実行する）。

4. 依存:
    標準: typing
    外部: なし
    プロジェクト内: simulator.main（build_interactor）/
                    simulator.domain.exceptions / simulator.framework.tester_settings（LOGGER）/
                    simulator.main.tester_settings.exit_codes / .kwargs_mapper / .window

実行対象の一致（重要・実測に基づく設計）:
    `BacktestController.run`（`adapter/controller.py:50-58` 実読）は `market_data.load` を
    **再実行**して `RunBacktestRequest` を自前で組み直すため、`build_interactor` が返した
    `request.trading_start` を渡さず、検証したバー列とも別インスタンスになる。本 facade は
    `controller.run` を使わず、**検証した request をそのままインタラクタで実行**する
    （「検証した対象と実行する対象を一致させる」）。インタラクタへの到達は
    `BacktestController.interactor`（公開プロパティ）経由である（ISSUE-395 / A-5:
    従来の非公開属性 `_interactor` 参照＝カプセル化の破れを公開取得点で解消した）。
"""
from __future__ import annotations

from typing import Any

from simulator.domain.exceptions import BacktestError
from simulator.framework.tester_settings import LOGGER
from simulator.main import build_interactor
from simulator.main.tester_settings.exit_codes import SUCCESS_EXIT_CODE, exit_code_for
from simulator.main.tester_settings.kwargs_mapper import (
    EngineBinding,
    TesterRunMetadata,
    build_run_metadata,
    effective_to_interactor_kwargs,
)
from simulator.main.tester_settings.window import resolve_data_window, verify_window_applied
from simulator.usecase.tester_settings import EffectiveSettings, TesterSettings


def run_effective_settings(
    effective: EffectiveSettings, binding: EngineBinding
) -> "tuple[int, Any, TesterRunMetadata]":
    """実行段: 実効設定の内容で 1 run を実行する（唯一の実行経路）。

    事前条件: ``binding`` の各値が供給済み（決済通貨・EA 固有引数は必須注入）。
    事後条件: 成功時は ``(0, 結果, メタ)``。バー系列を消費しない実行では
        ``result.stats.trades == 0``（メインループが 0 回）。
    例外: 設定・実行の失敗（`BacktestError` 系）を**そのまま送出する**。終了コードへの
        翻訳は呼出側 facade（`run_from_settings`）の責務であり、ここでは行わない
        （検証だけを行いたい呼出しが終了コードを解釈し直さずに済む）。
    """
    kwargs = effective_to_interactor_kwargs(effective, binding)
    controller, request = build_interactor(**kwargs)
    # 窓が実際に適用されたことを実行**前**に確認する（N-15・Fail-Stop の維持）。
    # EA 名は診断 3 点（§8.4.4）の 1 つ。`kwargs["ea_name"]` は写像層が `ea_stem` から
    # 導いて **`build_interactor` へ実際に渡した**識別子である。ここで `ea_stem(...)` を
    # 呼び直さないのは、算出式を 2 箇所に書けば「エンジンが受け取った EA」と「診断が
    # 示す EA」が将来ずれ得るためである。
    verify_window_applied(request, resolve_data_window(effective), ea_name=kwargs["ea_name"])
    # ISSUE-395 / A-5: 公開取得点 `BacktestController.interactor` 経由で到達する
    # （`controller.run` は検証した request を捨てて組み直すため使えない）。
    result = controller.interactor.execute(request)
    return SUCCESS_EXIT_CODE, result, build_run_metadata(effective)


def run_from_settings(
    settings: TesterSettings, binding: EngineBinding
) -> "tuple[int, Any | None, TesterRunMetadata]":
    """実行 A: Settings の内容で 1 run を実行する。

    事前条件: ``binding`` の各値が供給済み（決済通貨・EA 固有引数は必須注入）。
    事後条件: 終了コードは既存規約（成功 0 / `ConfigError` 2 / `BacktestError` 1）。
        近似実行（N-06・未実測 delay）と inert フィールドは `TesterRunMetadata` に
        必ず載せて返す（沈黙しない）。
    例外: 送出しない（設定・実行の失敗は終了コードで表す）。ただし本モジュールの
        内部不整合（`RuntimeError` 等）は握り潰さずそのまま伝播する。
    """
    effective = settings.effective()
    metadata = build_run_metadata(effective)
    if metadata.approximate:
        LOGGER.warning(
            "近似実行です: tick_model=%s reasons=%s",
            metadata.tick_model,
            ", ".join(metadata.approximation_reasons),
        )
    if metadata.inert_fields:
        LOGGER.warning(
            "inert なフィールドを %d 件検出しました（実行時に参照しません）: %s",
            len(metadata.inert_fields),
            ", ".join(metadata.inert_fields),
        )

    try:
        return run_effective_settings(effective, binding)
    except BacktestError as error:
        LOGGER.error(
            "Settings からの実行に失敗しました: %s",
            error,
            extra={"context": getattr(error, "context", {})},
        )
        return exit_code_for(error), None, metadata
