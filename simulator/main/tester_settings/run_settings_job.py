"""Settings から 1 ジョブを実行し成果物まで出す facade（Phase 8 裁定 T-1）。

1. 層名/責務:
    main 層（Composition Root）。`run_from_settings`（実行 A）が持たない 2 つ——
    **成果物の出力**と**拡張点への注入**——を足した実行経路を 1 本だけ提供する。
    窓の事後検証（N-15）と「検証した request をそのまま実行する」規律は
    `run_from_settings.execute_interactor_kwargs`（唯一の実行段）へ委譲し、写さない。
    終了コードの語彙は `exit_codes` が唯一の宣言場所である。

2. 含む構造:
    run_settings_job: 実効設定 ＋ 注入束 ＋ 出力先 ＋ 拡張 → (終了コード, 結果, 実行メタ)。

3. 元 MQL 対応:
    MT5 ストラテジーテスターの Start ボタン（Settings タブの内容で 1 パスを実行し、
    レポートを出力する）。`run_from_settings` はレポートを出さない点だけが違う。

4. 依存:
    標準: pathlib / typing
    外部: なし
    プロジェクト内: simulator.domain.exceptions（ConfigError）/
                    simulator.main（build_interactor 経由の実行段・`present_outputs`）/
                    simulator.main.tester_settings.exit_codes / .kwargs_mapper /
                    .run_from_settings

    `simulator.sim_ui` は import しない（不変条件 I-6）。銘柄仕様・EA 固有引数・データ
    パスの供給（`EngineBinding` の組立）は `sim_ui` 側 Composition Root の責務である。

なぜ `run_backtest` を呼ばないのか（実測に基づく設計）:
    `run_backtest` は `build_interactor(**meta)` を直接呼ぶため、Settings 由来の窓が
    実際に適用されたかの事後検証（N-15）を通らない。窓の指定が黙って無視された run が
    「成功」として出力まで進むと、別期間の結果が正しい結果に見える。

なぜ `run_from_settings` を呼ばないのか:
    同関数は成果物を書かず（`run_from_settings.py` 実読）、拡張点（Phase 6 の
    `strategy_override`・Phase 7 の `position_manager`・E-2 の `strategy_decorator`）への
    注入口も持たない。既存の呼出（CLI・検証用途）を壊さずに済ませるため、共通部だけを
    `execute_interactor_kwargs` として括り出し、両者がそれを呼ぶ形にしてある。

なぜ例外を握らないのか（`run_from_settings` との差）:
    ジョブ実行では**失敗理由が運用者へ届く**必要がある（子プロセスの stderr は起動器が
    捨てるため、理由はファイルに残すしかない）。ここで `BacktestError` を終了コードだけへ
    翻訳すると文言が消え、`run_backtest` と同じ「なぜ落ちたか分からない」経路を新設する
    ことになる。終了コードへの翻訳（`exit_codes.exit_code_for`＝単一ソース）は、理由を
    記録できる呼出側（`sim_ui/main/run_job.py`）が行う。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from simulator.domain.exceptions import ConfigError
from simulator.main import present_outputs
from simulator.main.tester_settings.exit_codes import SUCCESS_EXIT_CODE
from simulator.main.tester_settings.kwargs_mapper import (
    EngineBinding,
    TesterRunMetadata,
    build_run_metadata,
    effective_to_interactor_kwargs,
)
from simulator.main.tester_settings.run_from_settings import execute_interactor_kwargs
from simulator.usecase.tester_settings import EffectiveSettings


def run_settings_job(
    effective: EffectiveSettings,
    binding: EngineBinding,
    *,
    output_dir: Any,
    extensions: "Mapping[str, Any] | None" = None,
) -> "tuple[int, Any, TesterRunMetadata]":
    """実効設定の内容で 1 run を実行し、成果物を ``output_dir`` へ出す。

    事前条件: ``binding`` の各値が供給済み（決済通貨・EA 固有引数は必須注入）。
        ``extensions`` は `build_interactor` の**拡張点**（`strategy_override` /
        `position_manager` / `strategy_decorator`）への注入物。JSON スカラーで表せない
        実体であるため写像層（`.ini` 由来の値）は供給できず、呼出側が組んで渡す。
    事後条件: 成功時は ``(0, 結果, メタ)`` を返し、``output_dir`` に `stats.json` と
        `report.md` が出ている。
    例外: 設定・実行・出力の失敗（`BacktestError` 系）を**そのまま送出する**
        （モジュール docstring「なぜ例外を握らないのか」）。

    ``extensions`` は写像結果の**後**に適用する。拡張点は `.ini` からは供給されない
    引数であり、写像の像とは交わらない。交わる名前を渡すのは呼出側の誤りであるため、
    **ここで `ConfigError` にする**（`build_interactor` は受け付ける引数名なら値をそのまま
    使うため受け止めない。黙って後勝ちにすると「`.ini` に書いた条件と違う条件で走った
    結果」が成功として出力まで進む）。規律は写像層の `_accepted_ea_params`（`.ini` 由来の
    引数と重なる `ea_params` を拒む）と同一である。
    """
    kwargs = dict(effective_to_interactor_kwargs(effective, binding))
    injected = dict(extensions or {})
    conflicting = sorted(set(injected) & set(kwargs))
    if conflicting:
        raise ConfigError(
            "拡張点への注入が Settings 由来の引数と衝突しています: "
            f"{', '.join(conflicting)}",
            context={"conflicting": conflicting},
        )
    kwargs.update(injected)
    result = execute_interactor_kwargs(kwargs, effective)
    # 出力段は `run_backtest` と**同一実体**（T-1 で公開名にした `present_outputs`）。
    # 診断メタ（EA 名・銘柄）は写像層が実際に `build_interactor` へ渡した値を使う
    # ——別経路で導き直すと「実行した対象」と「レポートに載る対象」がずれ得る。
    present_outputs(result, Path(output_dir), ea_name=kwargs["ea_name"], symbol=kwargs["symbol"])
    return SUCCESS_EXIT_CODE, result, build_run_metadata(effective)
