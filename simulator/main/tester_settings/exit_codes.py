"""終了コードの語彙（成功値・例外翻訳表・翻訳関数）の**唯一の宣言場所**。

1. 層名/責務:
    main 層（Composition Root）。Settings 経路が返す終了コードを 1 箇所で宣言する。
    実行 facade（`run_from_settings`）と `MATH_CALCULATIONS` 経路
    （`math_calculations`）の双方が本モジュールを import する。

2. 含む構造:
    SUCCESS_EXIT_CODE : 成功時の終了コード。
    EXIT_CODES        : 例外 → 終了コードの対応（評価順を含む）。
    exit_code_for     : 例外 1 個を終了コードへ翻訳する。

3. 元 MQL 対応:
    なし（プロセスの終了コード規約）。

4. 依存:
    標準: なし
    外部: なし
    プロジェクト内: simulator.domain.exceptions のみ

なぜ独立モジュールなのか（🟡-3 の是正）:
    `run_from_settings` は `math_calculations` を import する。よって定数を
    `run_from_settings` に置いたままでは `math_calculations` から参照できない
    （循環 import）。その結果 `math_calculations` は生リテラル `0` を返しており、
    成功終了コードの宣言が 2 箇所に分かれていた。両者が依存できる**下位**へ
    宣言を移すことが、この重複の原因（置き場所の誤り）そのものの除去である。
    本モジュールは利用側を import しない（依存の向きが一方向であることは
    `test_tester_settings_exit_codes.py` が構文木で固定する）。

既存 2 箇所との関係:
    翻訳規約の出所は既存の `adapter/controller.py`（`BacktestController.run`）と
    `main/__init__.py`（`run_backtest`）である。本表はそれらを**書き写したもので
    はなく**、両者を実行して採取した値との一致を
    `test_exit_code_translation_parity.py` が突合する（値を人手で同期しない）。
"""
from __future__ import annotations

from simulator.domain.exceptions import BacktestError, ConfigError

#: 成功時の終了コード。
SUCCESS_EXIT_CODE: int = 0

#: 例外 → 終了コードの対応。`ConfigError` は `BacktestError` のサブクラスであるため
#: 先に評価する（順序が規約の一部）。
EXIT_CODES: "tuple[tuple[type[BacktestError], int], ...]" = (
    (ConfigError, 2),
    (BacktestError, 1),
)


def exit_code_for(error: BaseException) -> int:
    """既存規約に従って例外を終了コードへ翻訳する。

    事前条件: なし。
    事後条件: `EXIT_CODES` の**宣言順で最初に一致した**種別の値を返す。
    例外: どの種別にも一致しない例外は握り潰さずそのまま再送出する。
    """
    for error_type, code in EXIT_CODES:
        if isinstance(error, error_type):
            return code
    raise error
