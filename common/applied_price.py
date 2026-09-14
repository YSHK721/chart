"""適用価格（applied price）の計算（純粋ロジック・外部 I/O 非依存）。

①層名/責務:
    共有プリミティブ層。OHLC 配列から「適用価格」系列（1 次元の価格列）を生成する。
    移動平均・RSI・MACD・ADX など、価格系列を入力に取るあらゆる指標で再利用する横断
    ユーティリティ。特定の指標には属さない。

②含む構造:
    AppliedPrice          : MQL ENUM_APPLIED_PRICE と同一値の列挙。
    close_price / open_price / high_price / low_price : 単純な列選択。
    median_price / typical_price / weighted_price     : 算術合成。
    applied_price         : 列挙値で 8 種を切り替えるディスパッチャ（単一表 _APPLIED 経由）。
    SOURCE_TO_APPLIED     : UI ソース値（catalog の source enum 8 択）→ 種別の写像。
    OHLC_COLUMNS          : applied_price が要求する列名（引数順＝抽出順）の単一宣言。
    SYNTHETIC_SOURCE_TO_APPLIED : 合成が要る source（OHLC 列を直接指さないもの）→ 種別。
    resolve_source_prices : 表形式データ（列名大小不問）＋ソース値 → 価格系列の解決手続き。

③元 MQL 対応:
    MQL の ENUM_APPLIED_PRICE（PRICE_CLOSE / OPEN / HIGH / LOW / MEDIAN / TYPICAL /
    WEIGHTED）に相当。``MovingAverages.mqh`` は価格非依存のため、本モジュールは
    指標側の applied_price 機構を独立した共有層として切り出したもの。

④依存:
    標準: enum
    外部: numpy
    プロジェクト内: なし（core 層と同格の純粋ロジック）

    :func:`resolve_source_prices` は表（``columns`` と ``df[key].to_numpy(...)`` を持つもの）を
    **ダックタイピング**で受ける。pandas は import しない（本層の numpy のみ依存契約を保つ）。
"""

from __future__ import annotations

from enum import IntEnum

import numpy as np


class AppliedPrice(IntEnum):
    """適用価格の種別。値は MQL ``ENUM_APPLIED_PRICE`` と一致させてある。"""

    CLOSE = 1     # PRICE_CLOSE    終値
    OPEN = 2      # PRICE_OPEN     始値
    HIGH = 3      # PRICE_HIGH     高値
    LOW = 4       # PRICE_LOW      安値
    MEDIAN = 5    # PRICE_MEDIAN   (高値 + 安値) / 2
    TYPICAL = 6   # PRICE_TYPICAL  (高値 + 安値 + 終値) / 3
    WEIGHTED = 7  # PRICE_WEIGHTED (高値 + 安値 + 2*終値) / 4
    # --- MQL 外拡張（ENUM_APPLIED_PRICE に対応値なし）------------------------
    OHLC4 = 8     # (始値 + 高値 + 安値 + 終値) / 4。TradingView 等の合成価格。MQL には無いが
    #             # 共有層で正式対応し、全指標で再利用可能にする（案A）。


def _as_float(arr: np.ndarray) -> np.ndarray:
    """入力を float の numpy 配列へ変換する（内部ヘルパー）。"""
    return np.asarray(arr, dtype=float)


# ---------------------------------------------------------------------------
# 単純な列選択（PRICE_CLOSE / OPEN / HIGH / LOW）
# ---------------------------------------------------------------------------
def close_price(close: np.ndarray) -> np.ndarray:
    """終値系列（PRICE_CLOSE）を返す。"""
    return _as_float(close)


def open_price(open_: np.ndarray) -> np.ndarray:
    """始値系列（PRICE_OPEN）を返す。"""
    return _as_float(open_)


def high_price(high: np.ndarray) -> np.ndarray:
    """高値系列（PRICE_HIGH）を返す。"""
    return _as_float(high)


def low_price(low: np.ndarray) -> np.ndarray:
    """安値系列（PRICE_LOW）を返す。"""
    return _as_float(low)


# ---------------------------------------------------------------------------
# 算術合成（PRICE_MEDIAN / TYPICAL / WEIGHTED）
# ---------------------------------------------------------------------------
def median_price(high: np.ndarray, low: np.ndarray) -> np.ndarray:
    """中値系列（PRICE_MEDIAN）= (high + low) / 2 を返す。"""
    return (_as_float(high) + _as_float(low)) / 2.0


def typical_price(
    high: np.ndarray, low: np.ndarray, close: np.ndarray
) -> np.ndarray:
    """代表値系列（PRICE_TYPICAL）= (high + low + close) / 3 を返す。"""
    return (_as_float(high) + _as_float(low) + _as_float(close)) / 3.0


def weighted_price(
    high: np.ndarray, low: np.ndarray, close: np.ndarray
) -> np.ndarray:
    """加重終値系列（PRICE_WEIGHTED）= (high + low + 2*close) / 4 を返す。"""
    return (_as_float(high) + _as_float(low) + 2.0 * _as_float(close)) / 4.0


def ohlc4_price(
    open_: np.ndarray, high: np.ndarray, low: np.ndarray, close: np.ndarray
) -> np.ndarray:
    """OHLC 平均系列（OHLC4・MQL 外拡張）= (open + high + low + close) / 4 を返す。"""
    return (
        _as_float(open_) + _as_float(high) + _as_float(low) + _as_float(close)
    ) / 4.0


# ---------------------------------------------------------------------------
# 種別の単一表（ISSUE-479 Wave2 C-5）
# ---------------------------------------------------------------------------
# 同じ種別集合が enum 定義・ディスパッチャの if 連鎖・SOURCE_TO_APPLIED の 3 箇所で
# 列挙されていたため、種別 1 つの追加が 3 箇所の同時改変を要求していた（OCP 違反）。
# 本表を唯一の情報源とし、ディスパッチャは表引き、SOURCE_TO_APPLIED は表から導出する。
# キーは AppliedPrice（IntEnum ＝ int）で、enum 値の昇順に並べる。
# 値は (catalog の source キー, 抽出関数) の組。抽出関数は上の公開関数へ委譲する
# （式の定義は公開関数側 1 箇所のまま。ここでは引数の割り当てだけを持つ）。
_APPLIED: "dict[AppliedPrice, tuple[str, object]]" = {
    AppliedPrice.CLOSE: ("close", lambda o, h, lo, c: close_price(c)),
    AppliedPrice.OPEN: ("open", lambda o, h, lo, c: open_price(o)),
    AppliedPrice.HIGH: ("high", lambda o, h, lo, c: high_price(h)),
    AppliedPrice.LOW: ("low", lambda o, h, lo, c: low_price(lo)),
    AppliedPrice.MEDIAN: ("hl2", lambda o, h, lo, c: median_price(h, lo)),
    AppliedPrice.TYPICAL: ("hlc3", lambda o, h, lo, c: typical_price(h, lo, c)),
    AppliedPrice.WEIGHTED: ("hlcc4", lambda o, h, lo, c: weighted_price(h, lo, c)),
    AppliedPrice.OHLC4: ("ohlc4", lambda o, h, lo, c: ohlc4_price(o, h, lo, c)),
}


# ---------------------------------------------------------------------------
# ディスパッチャ
# ---------------------------------------------------------------------------
def applied_price(
    kind: AppliedPrice | int,
    open_: np.ndarray,
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
) -> np.ndarray:
    """``kind`` で指定した適用価格系列を OHLC 配列から生成する。

    MQL の ``applied_price`` 引数に相当する切り替え口。各指標は本関数で価格系列を
    用意し、移動平均などの計算関数へ渡す。

    Args:
        kind: 適用価格の種別（``AppliedPrice`` または同値の int）。
        open_: 始値配列。
        high: 高値配列。
        low: 安値配列。
        close: 終値配列。

    Returns:
        指定種別の価格系列（float の np.ndarray）。

    Raises:
        ValueError: ``kind`` が 8 種のいずれにも該当しない場合。
    """
    try:
        # int / float / np.int64 は AppliedPrice（IntEnum）と等値かつ同一ハッシュなので
        # そのまま解決される。非ハッシュ可能値は TypeError となり、下で ValueError へ揃える
        # （if 連鎖時代も未知種別として ValueError にしていた＝文言まで挙動不変）。
        entry = _APPLIED.get(kind)
    except TypeError:
        entry = None
    if entry is None:
        raise ValueError(f"未知の適用価格種別です: {kind!r}")
    return entry[1](open_, high, low, close)


# ---------------------------------------------------------------------------
# UI ソース値 → 適用価格種別（ISSUE-179 項目 4: 3 重複製の 1 本化）
# ---------------------------------------------------------------------------
# ``moving_averages/src/lwc_chart.py`` ↔ ``btlm_trail/src/core.py`` ↔ ``ma_marod/src/core.py``
# が個別に持っていた同一写像をここへ集約する（写像 1 行の追加が 3 ファイルの同時改変を
# 要求する状態＝OCP 違反の解消）。値・キーは移設元と完全に同一（無改変移設）。
#
# キーは catalog の source enum（小文字）。呼び出し側は ``str(source).lower()`` で引く。
# 種別の第 3 の列挙にならないよう、単一表 _APPLIED から導出する（キー・値・挿入順は同一）。
SOURCE_TO_APPLIED: dict[str, AppliedPrice] = {
    source: kind for kind, (source, _extract) in _APPLIED.items()
}

#: :func:`applied_price` が要求する列名（引数の並び＝抽出の並び）。
#: 「どの列が要るか」を解決手続きの側に書かせないための単一宣言（``col("open")`` 等の
#: リテラルが呼び出し側へ散らないようにする）。
OHLC_COLUMNS: tuple[str, ...] = ("open", "high", "low", "close")

#: 合成が必要な source（OHLC 列そのものを指さない source）→ 種別。
#: 既存列を素通しして合成だけを行う結線層（indicator_ui の tgp_btlm ソース 8 択化）が引く。
#: 種別集合の第 4 の列挙を作らないよう :data:`SOURCE_TO_APPLIED` から導出する
#: （キーが OHLC 列名と一致するものは「列そのもの」＝合成不要、という定義がそのまま式になる）。
SYNTHETIC_SOURCE_TO_APPLIED: dict[str, AppliedPrice] = {
    source: kind
    for source, kind in SOURCE_TO_APPLIED.items()
    if source not in OHLC_COLUMNS
}


# ---------------------------------------------------------------------------
# 表形式データからのソース解決（ISSUE-502 段階 2 D-6: 手続きの 4 重実装の 1 本化）
# ---------------------------------------------------------------------------
def resolve_source_prices(df, source: str) -> np.ndarray:
    """8 択ソース（close/open/high/low/hl2/hlc3/ohlc4/hlcc4）を float 配列で返す。

    ``btlm_trail/src/core.resolve_source`` / ``ma_marod/src/core.resolve_source`` /
    ``moving_averages/src/lwc_chart._source_prices`` が個別に持っていた同一手続き
    （AST 一致・実測）をここへ集約する。写像表 :data:`SOURCE_TO_APPLIED` は既に共有済み
    だったが、**手続き**（列名の小文字照合・欠落時の例外・抽出順）は共有されておらず、
    規則 1 つの変更が 3 ファイルの同時改変を要求していた（OCP 違反）。

    契約（移設元と完全同一・無改変移設）:
        * ``source`` は大小不問（``str(source).lower()`` で引く）。
        * 列名は大小不問（``str(c).lower()`` で照合するため非 str 列名でも AttributeError にならない）。
        * ``OHLC_COLUMNS`` の 4 列を**すべて**抽出する（ソース種別によらず存在を要求する）。

    Args:
        df: ``columns`` と ``df[key].to_numpy(dtype=...)`` を持つ表（pandas.DataFrame 等）。
        source: UI ソース値（catalog の source enum・大小不問）。

    Returns:
        指定ソースの価格系列（float64 の np.ndarray）。

    Raises:
        ValueError: ``source`` が 8 択のいずれでもない場合、または必要な列が欠落している場合。
    """
    kind = SOURCE_TO_APPLIED.get(str(source).lower())
    if kind is None:
        raise ValueError(f"未知のソースです: {source}")
    lower = {str(c).lower(): c for c in df.columns}

    def col(name: str) -> np.ndarray:
        if name not in lower:
            raise ValueError(f"ソース計算に必要な列がありません: {name}")
        return df[lower[name]].to_numpy(dtype=np.float64)

    return applied_price(kind, *(col(name) for name in OHLC_COLUMNS))
