"""marketdata.keep_last — keep-last（同一キーの最終出現を採る）規則の **唯一の実体**（ISSUE-479 F-6）。

なぜ 1 箇所に閉じるのか:
    「同一キーの重複を後勝ちで畳む」規則は本 repo に 5 箇所へ手書き複製されていた
    （tick_m1._dedupe_minutes / dataset._clamp_outlier_bars /
    ``tools/verify_pseudo_vwap.build_m1`` / ``marketdata/tools/dedupe_tick_m1._collect_last`` /
    ``tools/measure/issue449/probe_forming_long.load``）。複製は必ず取り残しを生む。
    規則を変えるときに触るべき箇所を本モジュール 1 つに固定する。
    この単一権威は ``marketdata/tests/test_keep_last.py`` が AST 走査で **強制**する。

依存ゼロ（重要）:
    本モジュールは **import 文を 1 つも持たない**。pandas はダックタイピングで扱い、
    ``pandas`` を使えない層（stdlib 純層・行 streaming の修復スクリプト）からも同じ規則を
    参照できるようにする。依存を足すと「どの層からも参照できる中立核」という目的が崩れる。

意味論（3 表現で同一）:
    どの入口でも「同一キーが複数回現れたら **最終出現**を採る」であり、採用行の集合と並びは
    表現によらず一致する（test_three_representations_adopt_the_same_rows が固定）。
"""

#: pandas の keep 引数に渡す値（後勝ち）。リテラルの散在を防ぐ唯一の定義。
KEEP_LAST = "last"


def dedupe_index_keep_last(df):
    """index の重複を keep-last で畳む。

    重複が無いときは ``df`` を **そのまま返す**（no-op 同一オブジェクト契約）。呼出側は
    「正常データでは 1 ビットも変わらない」ことに依存している（serving hygiene・M1 素材化）。
    重複があるときだけ真偽マスクを 1 枚発行して適用する（作って捨てる計算を出さない）。
    """
    if df.index.has_duplicates:
        return df[~df.index.duplicated(keep=KEEP_LAST)]
    return df


def dedupe_column_keep_last(df, column):
    """``column`` の重複を keep-last で畳む（1 走査・出力 1 枚）。"""
    return df.drop_duplicates(subset=[column], keep=KEEP_LAST)


def keep_last_by_key(pairs):
    """``(key, row)`` の並びを 1 度だけ消費し、key ごとの最終出現を持つ辞書を返す。

    保持量は一意 key 数（＝出力量）で頭打ちになる。全行を一旦配列へ載せないため、
    数千万行の CSV を行 streaming で畳む用途（marketdata/tools/dedupe_tick_m1.py）に使える。
    戻り値の並びは **初出順**（書出しの並びを決めるのは呼出側の責務）。
    """
    last = {}
    for key, row in pairs:
        last[key] = row
    return last
