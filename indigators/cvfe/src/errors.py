"""CVFE エラーコードと例外型（仕様 §3.3）。

層名/責務:
    最下層。仕様 §3.3 の 9 コードと、§4.3・§4.4 が要求する 2 つの WARN コードを定義する。
    本モジュールは他の CVFE モジュールへ依存しない（依存の末端）。

例外型は :class:`CvfeError`（``ValueError`` 派生）に一本化する。仕様 §3.3 は
「``ValueError`` を送出」と定めるのみでコード搬送の手段を定めないため、
呼び出し側が発生条件を分岐できるよう ``code`` 属性を持たせる。

依存: 標準ライブラリのみ。
"""

from __future__ import annotations

# 仕様 §3.3：例外を送出するもの。
E01_INSUFFICIENT_BARS = "E01_INSUFFICIENT_BARS"
E02_TICKS_NOT_MONOTONIC = "E02_TICKS_NOT_MONOTONIC"
E03_NONPOSITIVE_PRICE = "E03_NONPOSITIVE_PRICE"
E04_EDGES_NOT_MONOTONIC = "E04_EDGES_NOT_MONOTONIC"
E05_PARAM_RANGE = "E05_PARAM_RANGE"
E08_HAR_SINGULAR = "E08_HAR_SINGULAR"

# 仕様 §3.3：例外を送出せず縮退するもの（ログ対象）。
E06_EMPTY_BAR = "E06_EMPTY_BAR"
E07_QUALITY_FAIL = "E07_QUALITY_FAIL"
E09_NONFINITE_SIGMA = "E09_NONFINITE_SIGMA"

# 仕様 §4.3 / §4.4 が WARN ログを要求する 2 条件。仕様はコード名を与えていないため
# 本実装で命名する（§6 のログ要件「code の照合」を満たすために識別子が必要）。
W01_TSRV_NONPOSITIVE = "W01_TSRV_NONPOSITIVE"
W02_BPV_NONPOSITIVE = "W02_BPV_NONPOSITIVE"

#: HAR の説明変数 x4 = ln(1 + J_t/C_t) が学習標本内で厳密に定数となり、
#: 係数 β4 が識別できない場合の診断（ISSUE-205）。
W04_HAR_JUMP_COLUMN_CONSTANT = "W04_HAR_JUMP_COLUMN_CONSTANT"

#: HAR の学習標本から無効バー（E06 由来の nan 行）を除外したことの診断（ISSUE-211）。
W05_HAR_TRAINING_ROWS_DROPPED = "W05_HAR_TRAINING_ROWS_DROPPED"

#: 有効な σ̂ が 1 本も得られなかったことの診断（ISSUE-212）。
W06_NO_AVAILABLE_BARS = "W06_NO_AVAILABLE_BARS"

#: EWMA 初期値の算出に用いた 200 本目のギャップ保有バーが、予測開始バー以降に
#: 位置する場合の診断。仕様 §4.7-3 の文言を変更せず、look-ahead の発生のみ通知する。
W03_GAP_INIT_LOOKAHEAD = "W03_GAP_INIT_LOOKAHEAD"


class CvfeError(ValueError):
    """CVFE の入力・状態エラー。``code`` に §3.3 のエラーコードを持つ。

    ``violations``（ISSUE-283）: 呼び出し側が**機械可読**に扱える診断。文言の解析を強いない
    ための構造化フィールドで、既定は空。E01（履歴不足）では
    ``[{"code": "E01_INSUFFICIENT_BARS", "requiredBars": int, "actualBars": int}]`` を載せる。
    これにより上位（配信 API・フロント）は「あと何本必要か」を知り、**満たすまで要求を出さない**
    という判断ができる（毎バー失敗する要求を投げ続けない）。
    """

    __slots__ = ("code", "detail", "violations")

    def __init__(self, code: str, detail: str = "", violations=None) -> None:
        self.code = code
        self.detail = detail
        self.violations = list(violations) if violations else []
        super().__init__(f"{code}: {detail}" if detail else code)
