"""終了コードの語彙を `simulator.adapter.exit_codes` から**再輸出する**だけのモジュール。

1. 層名/責務:
    main 層（Composition Root）。Settings 経路の既存呼出側
    （`run_from_settings` / `math_calculations`）が使う import 経路を保つ。
    宣言は一切持たない——保持していると宣言が 2 箇所になる。

2. 含む構造:
    なし（再輸出のみ）。SUCCESS_EXIT_CODE / EXIT_CODES / exit_code_for の実体は
    `simulator.adapter.exit_codes` にある。

3. 元 MQL 対応:
    なし（プロセスの終了コード規約）。

4. 依存:
    標準: なし
    外部: なし
    プロジェクト内: simulator.adapter.exit_codes のみ

なぜ宣言を adapter へ移したのか（A-6）:
    翻訳規約は `adapter/controller.py`（`BacktestController.run`）も使う。宣言を
    main 層に置いたまま controller から委譲すると、adapter → main という内側から
    外側への import が生じる。`controller.py` 自身が「adapter 層は usecase + domain
    のみに依存する（framework / main は import しない）」と宣言しており、これに反する。
    内側 4 層から `simulator.main` への import が 0 件であることは
    `simulator/tests/unit/test_layer_dependency_direction.py` が構文木で固定する。
    逆向き（main → adapter）は Composition Root から内側への依存であり正しい。

なぜモジュールを消さずに残すのか:
    既存呼出側 2 件（`run_from_settings` / `math_calculations`）の import 経路を
    変えないため。宣言の所在（`adapter/exit_codes` に 1 件・本パッケージ内 0 件）は
    `simulator/tests/unit/test_tester_settings_exit_codes.py` が構文木で固定する。
"""
from __future__ import annotations

from simulator.adapter.exit_codes import (
    EXIT_CODES,
    SUCCESS_EXIT_CODE,
    exit_code_for,
)

__all__ = ["EXIT_CODES", "SUCCESS_EXIT_CODE", "exit_code_for"]
