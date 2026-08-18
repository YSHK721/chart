"""終了コードの語彙（成功値・例外翻訳表・翻訳関数）の**唯一の宣言場所**。

1. 層名/責務:
    adapter 層。プロセス起動側へ返す応答形式（終了コード）へ、domain の例外種別を
    翻訳する。翻訳規約の宣言はこのモジュールだけが持ち、他は import して使う。

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

なぜ adapter 層なのか（置き場所の根拠）:
    - domain ではない: 終了コードはプロセス起動側の応答形式＝偶有的性質であり、
      業務不変ルールではない。`simulator/domain` 配下に `exit_code` の参照は 0 件。
    - usecase ではない: `simulator/usecase` 配下に `exit_code` の参照は 0 件。
      ユースケースは結果オブジェクトを返し、整数コードへの翻訳には関与しない。
    - main ではない: `simulator/adapter/controller.py` が「adapter 層は usecase +
      domain のみに依存する（framework / main は import しない）」と宣言している。
      宣言を `main` に置いて adapter から委譲すると、内側 → 外側の依存が発生する。
      内側 4 層（adapter / usecase / domain / framework）から `simulator.main` への
      import は現状 0 件であり（`test_layer_dependency_direction.py` が固定する）、
      委譲はこの 0 件を 1 件に変える。
    - adapter である: 「domain の例外を外界の応答形式へ変換する」ことは
      入口アダプタ（`BacktestController`）が既に負っている責務そのものであり、
      その語彙を同層に置くのが最短の依存経路になる。`main` は adapter へ依存でき
      （外→内で正しい向き）、`main.tester_settings.exit_codes` は本モジュールを
      再輸出する。

拡張点（OCP）:
    新しい例外種別 → 終了コードの追加は `EXIT_CODES` に組を足すだけで済み、
    `exit_code_for` の分岐を書き換える必要はない。順序（サブクラスを先に置く）が
    規約の一部であることは `test_tester_settings_exit_codes.py` が固定する。
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
