"""素材（どのデータの・どの足か）の同一性（ISSUE-465）。

責務:
    供給された素材（OHLCV の DataFrame）へ「それが何の素材か」の識別を載せ、受け取る側が
    それを読めるようにするだけ。値にも計算にも関与しない。

なぜ素材そのものへ載せるのか:
    素材を受け取る面（増分計算の状態キャッシュ）は「どの足か」を引数で受け取らない。実際、
    ダッシュボードの計算供給は「adapter・指標 id・variant・素材・パラメータ」の 5 引数しか
    渡さず、足はパラメータから外して軸として持っている（dashboard_ui の SheetInstance）。
    呼出し面を一つずつ広げる代わりに、**素材を作った側が素材へ識別を載せる**。素材の由来を
    受け取り側が推測しない（推測すると窓の切り出し方や呼出し面の違いで別物になる）。

なぜ DataFrame の attrs か（実測 pandas 3.0.3・2026-08-30）:
    末尾切り出し・複製・行スライス・行の追加・並べ替え・連結（全入力が同一 attrs のとき）で
    伝播する。呼出し側が素材へ施す加工（窓の本数制限・形成中バーの注入・確定足との分割）を
    通しても識別が残る。オブジェクト寿命に依る識別（組込みの id）は使えない——素材は
    要求ごとに末尾切り出しで作り直されるため、同じデータ・同じ足でも毎回別物になる（実測）。

識別は**キーであって正しさの担保ではない**:
    取り違えても値は変わらない。増分器は確定プレフィクスの一致を見て流用可否を決めるため、
    識別が誤っていても再構築が起きるだけである（遅くなっても壊れない）。
    識別を持たない素材は None ＝従来どおり 1 つの入れ物を共有する。
"""

from __future__ import annotations

from typing import Any

#: 識別を置く DataFrame の attrs キー（書く側・読む側の単一の合意点）。
MATERIAL_ATTR = "material"


def label(df: Any, *, ref: Any, timeframe: Any) -> Any:
    """素材へ識別 (ref, timeframe) を載せて返す（``df`` をそのまま返す）。

    attrs を持たない相手（テストの偽物・DataFrame 以外）は識別を持たないまま返す。
    識別が無い＝従来どおり 1 つの入れ物という後方互換の既定へ落ちる。
    """
    attrs = getattr(df, "attrs", None)
    if isinstance(attrs, dict):
        attrs[MATERIAL_ATTR] = (
            None if ref is None else str(ref),
            None if timeframe is None else str(timeframe),
        )
    return df


def material_of(df: Any) -> Any:
    """素材の識別を返す（載っていなければ ``None``）。"""
    attrs = getattr(df, "attrs", None)
    if not isinstance(attrs, dict):
        return None
    return attrs.get(MATERIAL_ATTR)
