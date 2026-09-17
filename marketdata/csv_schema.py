"""csv_schema — ロールアップ互換 M1/上位足 CSV スキーマの単一定義（ISSUE-094 🟡-6）。

``jp225_m1.csv`` 系（:mod:`marketdata.tick_m1` が書き出す M1 原子）と上位足ロールアップ
（:mod:`marketdata.rollup` が書き出す ``<prefix>_<tf>.csv``）は **同一の loader 互換 CSV
スキーマ**（列順・date 書式）を共有する。従来は両モジュールが ``_HEADER`` / ``_DATE_FMT``
リテラルを各自に持ち「一致させること」コメントで人手同期していた（同一アクター＝CSV
スキーマ所有者の二重定義）。本モジュールがその唯一の規則源であり、両者は import 共有する。

値列台帳（ISSUE-511 前提 (b) 段階 3b）:
    「分 → 上位バーの縮約」の規則そのものを :data:`VALUE_COLUMN_LEDGER` が 1 行 1 列で持つ。
    かつて同じ規則が :mod:`marketdata.resample` の列別集約・:func:`marketdata.rollup._merge_agg`・
    :func:`marketdata.rollup.merge_same_period` の 3 箇所に手書きされており、列が 1 つ増えたとき
    どれか 1 つが取り残されても**出力は正しげなまま**だった（実際 merge_same_period は
    辞書リテラル 5 キーしか持たず、spread は列ごと消えていた）。台帳から導出すれば取り残しが
    起きない。導出していること自体は ``marketdata/tests/test_rollup_spread_aggregation.py`` が
    :mod:`marketdata.rollup` を AST 走査して強制する（走査の射程はこの 1 ファイルの書き出し・
    集約経路であり、他モジュールの手書き列挙は別の検定が要る）。

依存方向: 本モジュールは **依存ゼロ**（stdlib のみ）。:mod:`marketdata.tick_m1` /
:mod:`marketdata.rollup` / :mod:`marketdata.resample` が本モジュールを参照する（逆は無い・
循環禁止）。読み手の simulator.adapter.repository.ohlc_marketdata_csv も spread の列名を
本モジュールから引く（ISSUE-511 段階 2）。

SRP: 気配幅の**値**の規則（ティック → 分の縮約）は :mod:`marketdata.quote_spread` が持つ。
本モジュールが持つのは**分 → 上位バー**の縮約であり、変更理由が独立している。どちらも min を
使うが、その一致は検定で表明する事実であって依存にはしない（quote_spread を import しない）。
"""

from __future__ import annotations

from typing import Any, Callable, NamedTuple

# date 列の文字列書式（UTC 壁時計・秒精度）。
DATE_FMT = "%Y-%m-%d %H:%M:%S"

# 気配幅（ISSUE-511 段階 2・整数 points）の **任意** 列名。値の規則の唯一源は
#   marketdata.quote_spread（本モジュールは列名・列順・畳み方だけを持つ）。
#   point を注入した M1 だけが持つ（持たない CSV は従来と 1 バイトも変わらない）。
SPREAD_COLUMN = "spread"


# --------------------------------------------------------------------------- #
# 縮約のスカラ二項面（同じ規則の、pandas を通さない面）
#
# 列別集約（pandas の ``agg`` 名）と本二項面は **同じ縮約の 2 つの面**である。面が 2 つ要る
# のは実行時の都合（DataFrame をまとめて畳む面と、チャンク跨ぎの carry-over で 2 本の partial
# bar を畳む面）であって、規則が 2 つあるからではない。両者の一致は
# ``marketdata/tests/test_value_column_ledger.py`` が任意の分割で固定する。
# --------------------------------------------------------------------------- #
def _keep_first(prev: Any, new: Any) -> Any:
    """先に来た値を採る（``open``）。"""
    return prev


def _keep_last(prev: Any, new: Any) -> Any:
    """後に来た値を採る（close・未知列の既定）。"""
    return new


def _keep_max(prev: Any, new: Any) -> Any:
    return max(prev, new)


def _keep_min(prev: Any, new: Any) -> Any:
    return min(prev, new)


def _add(prev: Any, new: Any) -> Any:
    return prev + new


def _as_is(value: Any) -> Any:
    """未知列は型を変えずに素通しする（勝手に数値へ寄せない）。"""
    return value


def _is_missing(value: Any) -> bool:
    """欠損（非数・``None``）か。

    判定を pandas に頼らない（本モジュールは依存ゼロ＝stdlib のみ）。非数は自分自身と
    等しくないという性質で判り、``None`` だけを別に見る。実測（pandas 3.0.3 /
    numpy 2.4.6 / CPython 3.13.5）: 非数・numpy の非数・``None``・NaT・整数・浮動小数・
    負数・空文字列・文字列・無限大・numpy の整数 の 13 通りで pandas の isna と判定が
    一致する（不一致 0 件）。スカラ以外は射程外（呼び手が値列のセルだけを渡す）。
    """
    return value is None or value != value


def _skipping_missing(combine: "Callable[[Any, Any], Any]") -> "Callable[[Any, Any], Any]":
    """欠損オペランドを飛ばす二項面にする（列別集約と同じ規約）。

    なぜ要るか（実測 pandas 3.0.3）: 素の ``min`` は ``min(欠損, 70)`` が欠損・
    ``min(70, 欠損)`` が 70 になる＝**可換でない**。この二項面は
    :func:`marketdata.rollup.merge_same_period` がチャンク跨ぎの carry-over で使い、
    引数の順序は「前のチャンクの partial, 次のチャンクの partial」に固定されているため、
    可換でないと**どこでチャンクを切ったか**が出力を変える。実測（180 分・60〜99 分の
    spread が空欄・1 時間足）: ``chunk_rows`` 90/100 で 2 本目が空欄・120 以上で 70 と
    割れ、全件集計（70）と食い違った。

    一致の射程（検定が押さえている分だけ書く）: min・max・first・last は pandas の列別
    集約と同じく欠損を飛ばす。sum は**両方が欠損**のときだけ pandas が 0.0 を返すのに
    対し本規約は欠損を返す（この 1 点は一致しない）。両方が欠損なら欠損のまま残すのは、
    値を捏造しないためである。
    """

    def folded(prev: Any, new: Any) -> Any:
        if _is_missing(prev):
            return new
        if _is_missing(new):
            return prev
        return combine(prev, new)

    return folded


class ValueColumn(NamedTuple):
    """値列 1 つぶんの縮約規則。

    - ``agg``: pandas の列別集約名（resample / groupby へ渡す）。
    - ``combine``: 同じ縮約のスカラ二項面（carry-over が 2 本の partial bar を畳む）。
    - ``cast``: CSV へ書くときの型（spread は int・価格と件数は float）。
    - ``in_header``: ロールアップ CSV のヘッダへ出るか（vol は集約規則だけ持ち出ない）。
    - ``required``: 必須列か。必須列を欠いた bar は :class:`KeyError` で落とす（黙って
      列を落とさない）。任意列は「両方が持つときだけ」運ぶ。
    - ``updown``: 方向内訳（ティックの上昇数 / 下落数）か。:data:`UPDOWN_COLUMNS` の唯一源。
      「任意・合算・ヘッダ有り」という**条件の一致**では表さない。一致は偶然であり、任意の
      合算列（tools/pseudo_vwap の pv 等）が台帳へ増えた瞬間に方向内訳へ混入する
      （実測の根拠は :data:`UPDOWN_COLUMNS` の注記）。
    """

    name: str
    agg: str
    combine: Callable[[Any, Any], Any]
    cast: Callable[[Any], Any]
    in_header: bool = True
    required: bool = False
    updown: bool = False


#: 値列台帳（**この順序がヘッダの列順と集約規則の唯一源**）。
#:
#: vol を volume の直後に置いているのは :data:`SUM_COLUMNS` の内容・順序を従来と
#: 1 要素も変えないためである（vol はヘッダへ出ないので :data:`VALUE_COLUMNS` は不変）。
VALUE_COLUMN_LEDGER: "tuple[ValueColumn, ...]" = (
    ValueColumn("open", "first", _keep_first, float, required=True),
    ValueColumn("high", "max", _keep_max, float, required=True),
    ValueColumn("low", "min", _keep_min, float, required=True),
    ValueColumn("close", "last", _keep_last, float, required=True),
    ValueColumn("volume", "sum", _add, float, required=True),
    # 期間内の件数（合算）だがロールアップ CSV のヘッダへは出ない列。
    ValueColumn("vol", "sum", _add, float, in_header=False),
    # 方向内訳（tick 由来データだけが持つ任意列）。up=上昇ティック数 / dn=下落ティック数。
    ValueColumn("up", "sum", _add, float, updown=True),
    ValueColumn("dn", "sum", _add, float, updown=True),
    # 上位足の気配幅は、その足に含まれる M1 spread の**最小値**（MT5 の <SPREAD> を
    # オラクルとした実測・ISSUE-511 前提 (b)）。期間内の件数ではないので合算しない。
    ValueColumn(SPREAD_COLUMN, "min", _keep_min, int),
)

#: 未知列の既定（従来挙動の温存）。列名を知らなくても値を落とさず最終値を運ぶ。
_UNKNOWN = ValueColumn("", "last", _keep_last, _as_is)

_BY_NAME = {c.name: c for c in VALUE_COLUMN_LEDGER}


def _of(column: Any) -> ValueColumn:
    """列名（大小不問）に対応する台帳の行。未知列は :data:`_UNKNOWN`。"""
    return _BY_NAME.get(str(column).lower(), _UNKNOWN)


def agg_for(column: Any) -> str:
    """列別集約名（pandas の ``agg`` へ渡す）。未知列は ``"last"``（既存挙動）。"""
    return _of(column).agg


def combine_for(column: Any) -> "Callable[[Any, Any], Any]":
    """同じ縮約のスカラ二項面。未知列は「後の値を採る」（``"last"`` と同義）。

    欠損オペランドは飛ばす（:func:`_skipping_missing`）。畳みが可換・結合的でなければ、
    同じ素材でもチャンクの切り方で答えが変わる（実測と一致の射程は
    :func:`_skipping_missing` の docstring）。
    """
    return _skipping_missing(_of(column).combine)


def cast_for(column: Any) -> "Callable[[Any], Any]":
    """CSV へ書く型。spread は ``int``・価格と件数は ``float``・未知列は素通し。

    型を dtype に頼らないのは、pandas の ``resample().agg()`` が**空き期間を NaN で埋める
    ときに列ごと float へ昇格**させるためである（実測: 休場を挟むと int64 の spread が
    float64 になり ``71`` が ``71.0`` として書かれる。dropna は昇格の後に走るので
    取り消せない）。

    型を決める規則はこの 1 関数が持つが、**適用点は :mod:`marketdata.rollup` の 3 箇所**
    （`marketdata.rollup._bar_to_dict` / `marketdata.rollup._bar_to_csv_row` /
    `marketdata.rollup._write_rollup_df`）であり、それぞれ別の書き出し経路の整数表記を守る。
    3 点は一部で互いを遮蔽する（実測: `marketdata.rollup.stream_build` は
    `marketdata.rollup._bar_to_dict` で既に int へ戻すため、
    `marketdata.rollup._bar_to_csv_row` の適用だけを外しても出力は変わらない）。
    どの適用点がどの経路を守るかは rollup 側の
    各 docstring に書き、検定は
    ``marketdata/tests/test_rollup_spread_aggregation.py``（適用点ごとに 1 件）が持つ。
    """
    return _of(column).cast


def header_columns() -> "list[str]":
    """ロールアップ CSV のヘッダへ出る値列（台帳の順）。``VALUE_COLUMNS`` と同一内容。"""
    return [c.name for c in VALUE_COLUMN_LEDGER if c.in_header]


def required_columns() -> "list[str]":
    """必須値列（欠けていたら :class:`KeyError` で落とす列）。"""
    return [c.name for c in VALUE_COLUMN_LEDGER if c.required]


def optional_columns() -> "list[str]":
    """任意値列（持つデータだけが持つ列。両方が持つときだけ畳んで運ぶ）。"""
    return [c.name for c in VALUE_COLUMN_LEDGER if c.in_header and not c.required]


def updown_columns() -> "list[str]":
    """方向内訳の値列（台帳が ``updown`` で宣言した列。台帳の順）。"""
    return [c.name for c in VALUE_COLUMN_LEDGER if c.updown]


# --------------------------------------------------------------------------- #
# 台帳からの導出値（名前・内容・順序は従来と完全に同一＝既存データの書式不変）
# --------------------------------------------------------------------------- #

# HEADER から date（先頭）を除いた値列（open/high/low/close/volume）。
OHLCV_COLUMNS = required_columns()

# loader 互換 CSV の列（date + OHLCV）。:func:`marketdata.ohlc_csv_loader.load_ohlc_csv` が
# date を index、open/high/low/close/volume を値列として解決できる順序。
HEADER = ["date", *OHLCV_COLUMNS]

# 方向内訳（tick 由来データだけが持つ **任意** 列）。
#
# なぜ任意列か: 既存の CSV（jp225_m1 / jp225_daily / sample）は 1 分足 OHLC から作られており、
#   ティック単位の方向を復元できない。必須列にすると既存データが全滅するため、**持つデータだけが
#   持つ**追加列として末尾へ足す。読み側（marketdata.ohlc_csv_loader）は任意の追加列を素通しする
#   契約なので、無い CSV は従来どおり動く（列が増えても既存の列順・書式は 1 バイトも変わらない）。
#
# 方向内訳かどうかは台帳の ``updown`` が宣言する（:func:`updown_columns`）。「任意・合算・
#   ヘッダ有り」の条件一致で導いてはならない: 同じ条件を満たすが方向内訳ではない合算列
#   （tools/pseudo_vwap の pv 等。同モジュールは合算追加列を「SUM_COLUMNS へ入る予定」と
#   宣言している）が台帳へ増えた瞬間、marketdata.tick_m1 / marketdata.tools.dedupe_tick_m1 が
#   それを方向内訳として扱い始める。
#
# 実測（是正前の導出へ仮の pv を 1 行足した反事実・段階 3b 工程 4）: UPDOWN_COLUMNS は
#   ``['up', 'dn', 'pv']`` になり、既存検定は落ちる。ただし落ち方は**列形の食い違い**
#   （``date,...,up,dn`` に対し期待が ``date,...,up,dn,pv``）であって、「方向内訳へ混入した」
#   ことは告げない。概念は、条件の一致からではなく台帳の宣言から引く。
UPDOWN_COLUMNS = updown_columns()

# 既知値列の順序の唯一源（OHLCV → up/dn → spread）。
VALUE_COLUMNS = header_columns()

# 合算集約する列（上位足へ resample するとき "last" でなく "sum" を使うもの）。
#   volume と同じ性質（期間内の件数）を持つ列を台帳が宣言する（規則の二重定義を避ける）。
SUM_COLUMNS = [c.name for c in VALUE_COLUMN_LEDGER if c.agg == "sum"]


def header_for(columns) -> list[str]:
    """値列の集合から CSV ヘッダ（date + 既知列の順）を返す。

    既知列（:data:`VALUE_COLUMNS`＝OHLCV → up/dn → spread）の順序を固定し、未知列は末尾へ
    出現順で置く。up/dn・spread を持たないデータでは :data:`HEADER` と完全一致する
    （既存 CSV の書式不変）。
    """
    have = {str(c).lower() for c in columns}
    ordered = [c for c in VALUE_COLUMNS if c in have]
    rest = [str(c) for c in columns if str(c).lower() not in set(ordered)]
    return [HEADER[0], *ordered, *rest]


__all__ = [
    "HEADER", "OHLCV_COLUMNS", "DATE_FMT",
    "UPDOWN_COLUMNS", "SPREAD_COLUMN", "VALUE_COLUMNS", "SUM_COLUMNS", "header_for",
    "ValueColumn", "VALUE_COLUMN_LEDGER",
    "agg_for", "combine_for", "cast_for",
    "header_columns", "required_columns", "optional_columns", "updown_columns",
]
