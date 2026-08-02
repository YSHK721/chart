"""ma_marod コア（純粋ロジック・外部 I/O 非依存・numpy のみ）。

層名/責務:
    core 層。MA_MAROD（移動平均乖離率・MA 種別選択式）を算出する。基準線（分母）は
    moving_averages core の 4 種バッファ関数（sma/ema/smma/lwma）を「参照実装」として
    そのまま再利用する（無改変参照・OCP）。バンド・外れ値イベント分位は共有プリミティブ
    ``common.marod_bands``（quantile_bands / sigma_band / outlier_event_quantiles）へ
    委譲する（SOLID 是正 🟡-10: 兄弟具象 btlm_trail_marod への依存を common で対称化）。

    MA_MAROD_t = (price_t - ma_t) / ma_t * 100
        price = 単一の解決済みソース配列（8 択・既定 close。解決写像は moving_averages
                lwc_chart の _SOURCE_TO_APPLIED と同一＝計算の原子を MA と同期・§2.1）
        ma_t  = MA(price, ma_type, length)[t]（moving_averages _main_ma 規約に一致:
                ema は先頭から有効・sma/smma/lwma は先頭 length-1 本 NaN）

    ソース解決は 1 回だけ行い、同一の price 配列を分子と MA 入力の両方に供給する
    （分子と基準線でソースが乖離する余地を構造的に排除）。因果・非リペイント
    （確定バーの値が後続データ追加で不変）は全 4 種別で成立する（前進逐次計算）。
    0 除算（ma == 0）は errstate で抑制し、生じた inf/NaN は NaN に落として描画から除外。

参照機構（無改変・btlm_trail_marod ``_load_btlm_trail`` の前例踏襲）:
    基準線 MA の供給元は :class:`MovingAverageReference` Protocol（本 core が所有する抽象）で
    表す。:func:`set_moving_average_reference` で外側（Composition Root）から具象を注入でき、
    **未注入時は従来どおり moving_averages の core.py を動的ロードする**（既存呼出元の挙動は
    完全不変・ISSUE-176）。フォールバックの動的ロードは共有ローダ ``common.module_loader``
    （ロック付き）へ委譲し、importlib / sys の機構は本 core が保持しない（SRP / DIP）。

依存:
    標準: __future__, pathlib, typing / 外部: numpy /
    プロジェクト内: common.applied_price・common.marod_bands・common.module_loader
    （絶対 import）、moving_averages/src/core.py（既定の参照具象・動的ロード）
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol, runtime_checkable

import numpy as np

from common.applied_price import SOURCE_TO_APPLIED, AppliedPrice, applied_price
from common import marod_bands as _bands
from common import module_loader as _module_loader

# 既定パラメータ（ma_type/length はユーザー裁定 2026-07-21: moving_averages 既定と対称の
#   ema・length は sma 実測最良の 50。バンド既定は btlm_trail_marod と同値）。
DEFAULT_SOURCE: str = "close"
DEFAULT_MA_TYPE: str = "ema"
DEFAULT_LENGTH: int = 50

# ローリング σ / 分位バンドの既定（btlm_trail_marod core と同値・カタログ公開も同構成）。
DEFAULT_WINDOW_N: int = 500            # σ・分位の因果ローリング窓（本数・動的変更可）
DEFAULT_Q_LOW: float = 0.05            # 下側分位（btlm_trail_marod DEFAULT_Q_LOW と同値）
DEFAULT_Q_HIGH: float = 0.95           # 上側分位（btlm_trail_marod DEFAULT_Q_HIGH と同値）
SIGMA_MULT: float = 2.0               # σ バンド倍率（btlm_trail_marod SIGMA_MULT と同値）
# 外れ値イベント分位（ユーザー裁定 2026-07-21）: 「正常バンド（q_low/q_high）を超えた値＝
#   外れ値イベント」の集合に対する因果分位。実装の正は btlm_trail_marod core の系列汎用関数
#   marod_outlier_event_quantiles（バンド関数群と同様の無改変参照）。既定値も参照実装と同値
#   （同値性はテストで恒久固定）。
DEFAULT_Q_OUT: float = 0.99
DEFAULT_K_EVENTS: int = 50             # ローリング側の直近観測件数（分散非定常対策・実測 2026-07-20）
DEFAULT_EVENT_AGG: str = "episode"     # episode＝エピソード極値（既定）／bar＝旧方式（復帰用）

# 最小 length（参照実装 *_on_buffer は period<=1 を計算しない契約 → 本指標は明示エラー）。
_MIN_LENGTH: int = 2

# UI ソース値 → 共有 AppliedPrice 種別。moving_averages/src/lwc_chart.py の
#   _SOURCE_TO_APPLIED と同一写像（同一性はテストで恒久固定＝計算の原子の同期・§2.1）。
#   写像の実体は共有プリミティブへ 1 本化した（ISSUE-179 項目 4）。
_SOURCE_TO_APPLIED: dict[str, AppliedPrice] = SOURCE_TO_APPLIED

# ma_marod/src/core.py → parents[2] = indigators/。参照する 2 パッケージの core.py。
_INDIGATORS_DIR = Path(__file__).resolve().parents[2]
_MOVING_AVERAGES_CORE = _INDIGATORS_DIR / "moving_averages" / "src" / "core.py"
_MOVING_AVERAGES_MODNAME = "_moving_averages_src_for_ma_marod"

# 有効開始規約（moving_averages lwc_chart の _FROM_ZERO と同一: ema のみ先頭から有効）。
_FROM_ZERO: frozenset[str] = frozenset({"ema"})


@runtime_checkable
class MovingAverageReference(Protocol):
    """基準線 MA の供給元が満たすべき契約（本 core が所有する抽象）。

    既定の具象は moving_averages の core.py（同名メソッドをモジュール関数として公開）。
    シグネチャは MQL 由来の ``*_on_buffer(rates_total, prev_calculated, begin, period,
    price, buffer)``（``buffer`` へ書き込む）。
    """

    def simple_ma_on_buffer(self, rates_total, prev_calculated, begin, period, price, buffer): ...

    def exponential_ma_on_buffer(self, rates_total, prev_calculated, begin, period, price, buffer): ...

    def smoothed_ma_on_buffer(self, rates_total, prev_calculated, begin, period, price, buffer): ...

    def linear_weighted_ma_on_buffer(self, rates_total, prev_calculated, begin, period, price, buffer): ...


# 注入された具象（None＝未注入＝動的ロードへフォールバック）。差し替えは set_moving_average_reference。
_MOVING_AVERAGE_REF: "MovingAverageReference | None" = None


def _load_module(modname: str, path: Path):
    """core.py をファイルパスから一意名でロードする（read-only・無改変参照・キャッシュ付き）。

    実体は共有ローダ ``common.module_loader.load_module``（ロック付き・ISSUE-176）。
    """
    return _module_loader.load_module(modname, path)


def _load_moving_averages():
    """moving_averages core（4 種 MA バッファ関数）をロードする。"""
    return _load_module(_MOVING_AVERAGES_MODNAME, _MOVING_AVERAGES_CORE)


def set_moving_average_reference(ref: "MovingAverageReference | None") -> None:
    """基準線 MA の供給元を注入する（``None`` で既定＝moving_averages 動的ロードへ戻す）。

    Raises:
        TypeError: ``MovingAverageReference``（4 種バッファ関数）を満たさないとき。
    """
    global _MOVING_AVERAGE_REF
    if ref is not None and not isinstance(ref, MovingAverageReference):
        raise TypeError(
            "MovingAverageReference（sma/ema/smma/lwma の *_on_buffer）を満たしません: "
            f"{type(ref).__name__}"
        )
    _MOVING_AVERAGE_REF = ref


def moving_average_reference() -> "MovingAverageReference":
    """現在有効な MA 供給元を返す（未注入時は moving_averages の動的ロード＝従来挙動）。"""
    ref = _MOVING_AVERAGE_REF
    if ref is not None:
        return ref
    return _load_moving_averages()


def _ma_funcs() -> dict:
    """ma_type → 参照実装バッファ関数（moving_averages lwc_chart _MA_FUNCS と同一写像）。"""
    mv = moving_average_reference()
    return {
        "sma": mv.simple_ma_on_buffer,
        "ema": mv.exponential_ma_on_buffer,
        "smma": mv.smoothed_ma_on_buffer,
        "lwma": mv.linear_weighted_ma_on_buffer,
    }


def resolve_source(df, source: str) -> np.ndarray:
    """8 択ソース（close/open/high/low/hl2/hlc3/ohlc4/hlcc4）を float 配列で返す。

    写像は moving_averages と同一（``_SOURCE_TO_APPLIED``）で、合成価格の計算は共有
    ``applied_price`` に委譲する（計算の原子を基準線 MA と同期）。列名の大小は問わない。
    """
    kind = _SOURCE_TO_APPLIED.get(str(source).lower())
    if kind is None:
        raise ValueError(f"未知のソースです: {source}")
    lower = {str(c).lower(): c for c in df.columns}

    def col(name: str) -> np.ndarray:
        if name not in lower:
            raise ValueError(f"ソース計算に必要な列がありません: {name}")
        return df[lower[name]].to_numpy(dtype=np.float64)

    return applied_price(kind, col("open"), col("high"), col("low"), col("close"))


def ma_series(price: np.ndarray, ma_type: str, length: int) -> np.ndarray:
    """基準線 MA を参照実装バッファ関数で計算する（moving_averages _main_ma 規約に一致）。

    Args:
        price: 解決済みソース価格配列（昇順）。
        ma_type: sma / ema / smma / lwma。
        length: 平均本数（min 2）。

    Returns:
        MA 系列（float・長さ n。ema は先頭から有効・他は先頭 length-1 本 NaN）。

    Raises:
        ValueError: ma_type 不正、または length < 2 のとき。
    """
    funcs = _ma_funcs()
    fn = funcs.get(str(ma_type).lower())
    if fn is None:
        raise ValueError(f"未知の ma_type です: {ma_type}（sma/ema/smma/lwma）")
    if int(length) < _MIN_LENGTH:
        raise ValueError(f"length は {_MIN_LENGTH} 以上が必要です: length={length}")
    p = np.asarray(price, dtype=np.float64).ravel()
    n = p.size
    buffer = np.zeros(n, dtype=np.float64)
    fn(n, 0, 0, int(length), p, buffer)
    valid_from = 0 if str(ma_type).lower() in _FROM_ZERO else int(length) - 1
    if valid_from > 0:
        buffer[:valid_from] = np.nan
    return buffer


def ma_marod_series(
    df,
    *,
    source: str = DEFAULT_SOURCE,
    ma_type: str = DEFAULT_MA_TYPE,
    length: int = DEFAULT_LENGTH,
) -> np.ndarray:
    """MA_MAROD（移動平均乖離率・%）系列を返す（入力バーと同順・同長）。

    ``MA_MAROD_t = (price_t - ma_t) / ma_t * 100``。ソース解決は 1 回だけ行い、
    同一の price 配列を分子と MA 入力の両方に供給する（単一経路・§2.1）。

    Args:
        df: OHLC DataFrame（列名大小不問）。
        source: 8 択ソース（既定 close）。
        ma_type: 基準線 MA 種別（sma/ema/smma/lwma・既定 ema）。
        length: MA 本数（既定 50・min 2）。

    Returns:
        MA_MAROD 系列（float・warm-up と未定義は NaN・inf は残さない）。

    Raises:
        ValueError: source / ma_type 不正、または length < 2 のとき。
    """
    price = resolve_source(df, source)  # 単一の解決済み配列（分子と MA 入力で共用）。
    ma = ma_series(price, ma_type, length)
    # 乖離率の式は共有プリミティブ（common.marod_bands.marod_percent）が唯一の定義。
    #   warm-up（ma=NaN）・0 除算（ma=0→inf）由来の非有限値は NaN に落として描画除外。
    return _bands.marod_percent(price, ma)


def ma_marod_quantile_bands(
    marod: np.ndarray,
    *,
    window_n: int = DEFAULT_WINDOW_N,
    q_low: float = DEFAULT_Q_LOW,
    q_high: float = DEFAULT_Q_HIGH,
) -> tuple[np.ndarray, np.ndarray]:
    """因果ローリング経験分位バンド。共有プリミティブ ``common.marod_bands.quantile_bands`` へ委譲。"""
    return _bands.quantile_bands(marod, window_n=window_n, q_low=q_low, q_high=q_high)


def ma_marod_outlier_event_quantiles(
    marod: np.ndarray,
    *,
    window_n: int = DEFAULT_WINDOW_N,
    q_low: float = DEFAULT_Q_LOW,
    q_high: float = DEFAULT_Q_HIGH,
    q_out: float | None = DEFAULT_Q_OUT,
    k_events: int = DEFAULT_K_EVENTS,
    event_agg: str = DEFAULT_EVENT_AGG,
    bands=None,
    include_all: bool = True,
) -> dict[str, np.ndarray]:
    """外れ値イベント分位水準。共有ラッパ ``common.marod_bands.outlier_event_quantiles`` へ委譲。

    仕様・契約は参照実装のとおり（イベント定義・episode/bar 集計・因果境界・戻り値キー・
    例外）。ユーザー裁定 2026-07-21 で ma_marod に確立した設計を系列汎用化して参照する。
    """
    return _bands.outlier_event_quantiles(
        marod, window_n=window_n, q_low=q_low, q_high=q_high,
        q_out=q_out, k_events=k_events, event_agg=event_agg,
        bands=bands, include_all=include_all,
    )


def ma_marod_sigma_band(
    marod: np.ndarray,
    *,
    window_n: int = DEFAULT_WINDOW_N,
    mult: float = SIGMA_MULT,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """因果ローリング σ バンド。共有プリミティブ ``common.marod_bands.sigma_band`` へ委譲。"""
    return _bands.sigma_band(marod, window_n=window_n, mult=mult)
