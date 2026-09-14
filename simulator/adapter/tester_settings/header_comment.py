"""`.ini` 1 行目コメントの読取専用解析（API-08・F-18）。

1. 層名/責務:
    adapter 層。MT5 が自動生成する 1 行目コメントを構成要素へ分解する。
    **読取専用**であり、コメントの生成・書き換えは行わない（R3）。
    解析結果は正典ではない（設定の正典は `[Tester]` セクション）。用途は
    「コメントと `[Tester]` の値が整合するか」を突き合わせる検証補助に限る
    （基本設計 §6.1）。したがって解析不能時は例外を送出せず ``None`` を返す
    （内部設計 §4.5.3）。

2. 含む構造:
    HeaderCommentInfo   : 分解結果の DTO（7 項目）。
    parse_header_comment: 解析関数（唯一の公開関数）。

3. 元 MQL 対応:
    MT5 が `.ini` 保存時に書き込む 1 行目コメント。corpus 44 件の実測書式（F-18）:

        ;{テスト種別}: {対象名}, {Symbol} {Period}, {Model 語}, {期間語}[, with forward period]

    実測されたテスト種別は 4 種（``Expert Advisor visual test`` /
    ``Indicator visual test`` / ``Full optimization`` / ``Genetic optimization``）、
    Model 語は 4 種（``every tick`` / ``m1 ohlc`` / ``open prices`` / ``real ticks``）、
    期間語は ``entire history`` / ``last year`` / ``YYYY.MM.DD - YYYY.MM.DD``。
    ⚠️ 語彙の対応表（Model 語 → ``TickModel`` 等）は本モジュールに持たない。
    突合はテスト側（T-05）の責務であり、ここへ写すと同じ知識が 2 箇所になる。

4. 依存:
    標準: dataclasses
    外部: なし
    プロジェクト内: なし（生の文字列だけを扱うため DTO・列挙にも依存しない）
"""
from __future__ import annotations

from dataclasses import dataclass

#: コメント行の先頭文字（R3）。
_COMMENT_PREFIX: str = ";"
#: テスト種別と本体の区切り（実測 44/44 件）。
_KIND_SEPARATOR: str = ": "
#: 本体の項目区切り（実測 44/44 件）。
_FIELD_SEPARATOR: str = ", "
#: forward 期間ありを示す末尾語（F-9。実測 14/44 件）。
_FORWARD_SUFFIX: str = ", with forward period"
#: 本体（forward 語を除いた部分）の項目数（対象名 / `Symbol Period` / Model 語 / 期間語）。
_BODY_FIELD_COUNT: int = 4
#: `Symbol` と `Period` の区切り（実測 44/44 件が半角空白 1 個）。
_SYMBOL_PERIOD_SEPARATOR: str = " "


@dataclass(frozen=True)
class HeaderCommentInfo:
    """1 行目コメントの分解結果（内部設計 §6 補助 DTO）。

    各項目は**原文のまま**の文字列である（列挙へ写像しない）。``with_forward`` のみ
    末尾語の有無から導いた真偽値。
    """

    test_kind: str
    subject: str
    symbol: str
    period: str
    model_word: str
    period_word: str
    with_forward: bool


def parse_header_comment(comment: str | None) -> HeaderCommentInfo | None:
    """1 行目コメントを分解する（API-08）。

    事前条件: なし（``None`` を渡してよい）。
    事後条件: F-18 の書式に一致すれば ``HeaderCommentInfo``、一致しなければ
    ``None`` を返す。**例外は一切送出しない**（検証補助であり正典ではないため。
    内部設計 §4.5.3）。

    分解は右側の固定構造から行う（末尾の forward 語を外し、残りを ``", "`` で
    ちょうど 4 項目に分ける）。対象名に ``", "`` を含む例は corpus 44 件に存在
    しないため区切りの曖昧性は生じないが、仮に存在すれば項目数が合わず ``None``
    になる（誤った分解結果を返さない方向へ倒す）。
    """
    if comment is None or not comment.startswith(_COMMENT_PREFIX):
        return None

    body = comment[len(_COMMENT_PREFIX) :]
    if _KIND_SEPARATOR not in body:
        return None
    test_kind, rest = body.split(_KIND_SEPARATOR, 1)

    with_forward = rest.endswith(_FORWARD_SUFFIX)
    if with_forward:
        rest = rest[: -len(_FORWARD_SUFFIX)]

    fields = rest.split(_FIELD_SEPARATOR)
    if len(fields) != _BODY_FIELD_COUNT:
        return None
    subject, symbol_period, model_word, period_word = fields

    if _SYMBOL_PERIOD_SEPARATOR not in symbol_period:
        return None
    symbol, period = symbol_period.rsplit(_SYMBOL_PERIOD_SEPARATOR, 1)

    if not (test_kind and subject and symbol and period and model_word and period_word):
        return None

    return HeaderCommentInfo(
        test_kind=test_kind,
        subject=subject,
        symbol=symbol,
        period=period,
        model_word=model_word,
        period_word=period_word,
        with_forward=with_forward,
    )
