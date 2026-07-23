"""btlm_trail_marod コア（純粋ロジック・外部 I/O 非依存・numpy のみ）。

層名/責務:
    core 層。MAROD（移動平均乖離率）を算出する。基準線（分母）と対象価格（分子）は
    いずれも btlm_trail core を「参照実装」としてそのまま再利用する（無改変参照・OCP）。

    MAROD_t = (source_t - mean_t) / mean_t * 100
        source_t = btlm_trail.resolve_source(df, source)[t]          （8 択ソース・既定 close）
        mean_t   = btlm_trail.rolling_ols_window_end(source, maxbars)[0][t]  （OLS 窓末尾トレンド）

    因果・非リペイント（確定バーの値が後続データ追加で不変）は btlm_trail core の同一機構に
    より自動的に成立する。mean が NaN の warm-up 区間（窓 < 3 本）は MAROD も NaN。0 除算
    （mean == 0）は errstate で抑制し、生じた inf/NaN は NaN に落として描画から除外する。

参照機構（無改変・撤去済み btlm_trail/src/ma_reference.py の前例踏襲）:
    btlm_trail のパッケージ src は top-level 名 ``src`` を用い ``import src`` では衝突するため、
    その ``__init__.py`` をファイルパスから一意名でロードして公開関数をそのまま利用する
    （btlm_trail src は read-only・無改変）。相対 import（``from .trail import`` 等）を解決
    できるよう ``submodule_search_locations`` を与え、exec 前に sys.modules へ登録する。

依存:
    標準: __future__, importlib, sys, pathlib / 外部: numpy /
    プロジェクト内: btlm_trail/src（動的ロード。内部で common.applied_price を絶対 import）、
    common.event_quantiles（外れ値イベント分位の共有プリミティブ・絶対 import）
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import numpy as np

from common import event_quantiles as _evq
from common import marod_bands as _bands

# 既定パラメータ（btlm_trail core DEFAULT_MAXBARS と同値・8 択ソース既定 close）。
DEFAULT_SOURCE: str = "close"
DEFAULT_MAXBARS: int = 100

# ローリング σ / 分位バンドの既定（btlm_trail core DEFAULT_EMP_N=500 と対称・分位も参照既定同値）。
DEFAULT_WINDOW_N: int = 500            # σ・分位の因果ローリング窓（本数・動的変更可）
DEFAULT_Q_LOW: float = 0.05            # 下側分位（btlm_trail DEFAULT_Q_LOW と同値）
DEFAULT_Q_HIGH: float = 0.95           # 上側分位（btlm_trail DEFAULT_Q_HIGH と同値）
SIGMA_MULT: float = 2.0               # σ バンド倍率（mean ± SIGMA_MULT·σ・5%/95% 分位と対称）
_MIN_STAT_OBS: int = _bands.MIN_STAT_OBS  # σ（ddof=1）・分位に必要な最小有限本数（共有 common.marod_bands と単一定義）

# 外れ値イベント分位の既定（ユーザー裁定 2026-07-21）。実装と既定値の正は共有プリミティブ
#   common.event_quantiles（指標横断の単一情報源）。本モジュールは系列レベル API
#   （marod_outlier_event_quantiles＝バンド算出＋委譲）を公開する。
DEFAULT_Q_OUT: float = _evq.DEFAULT_Q_OUT
DEFAULT_K_EVENTS: int = _evq.DEFAULT_K_EVENTS
DEFAULT_EVENT_AGG: str = _evq.DEFAULT_EVENT_AGG

# btlm_trail_marod/src/core.py → parents[2] = indigators/。参照する btlm_trail の src。
_BTLM_TRAIL_SRC = Path(__file__).resolve().parents[2] / "btlm_trail" / "src"
_BTLM_TRAIL_MODNAME = "_btlm_trail_src_for_marod"


def _load_btlm_trail():
    """btlm_trail の src パッケージを一意名でロードする（``src`` 名衝突を回避・無改変参照）。

    キャッシュ: ``sys.modules[_BTLM_TRAIL_MODNAME]`` が存在すれば再 exec せず返す。
    """
    cached = sys.modules.get(_BTLM_TRAIL_MODNAME)
    if cached is not None:
        return cached
    spec = importlib.util.spec_from_file_location(
        _BTLM_TRAIL_MODNAME,
        _BTLM_TRAIL_SRC / "__init__.py",
        submodule_search_locations=[str(_BTLM_TRAIL_SRC)],
    )
    if spec is None or spec.loader is None:  # pragma: no cover - 環境異常（spec 解決不能）
        raise ImportError(f"btlm_trail core を読み込めません: {_BTLM_TRAIL_SRC}")
    module = importlib.util.module_from_spec(spec)
    # exec 前に登録する（btlm_trail src 内の相対 import が自モジュールを参照できるように）。
    sys.modules[_BTLM_TRAIL_MODNAME] = module
    spec.loader.exec_module(module)
    return module


def marod_series(df, *, source: str = DEFAULT_SOURCE, maxbars: int = DEFAULT_MAXBARS) -> np.ndarray:
    """MAROD（移動平均乖離率・%）系列を返す（入力バーと同順・同長）。

    ``MAROD_t = (source_t - mean_t) / mean_t * 100``。source/mean は btlm_trail core を
    そのまま参照する（自前の source/maxbars で OLS トレンドを算出＝独立インスタンス）。

    Args:
        df: OHLC DataFrame（列名大小不問）。
        source: 8 択ソース（close/open/high/low/hl2/hlc3/ohlc4/hlcc4・既定 close）。
        maxbars: 回帰窓（既定 100・min 3）。

    Returns:
        MAROD 系列（float・warm-up と未定義は NaN・inf は残さない）。

    Raises:
        ValueError: source 不正、または maxbars < 3（btlm_trail core の契約）。
    """
    bt = _load_btlm_trail()
    prices = np.asarray(bt.resolve_source(df, source), dtype=np.float64).ravel()
    mean = np.asarray(bt.rolling_ols_window_end(prices, maxbars)[0], dtype=np.float64).ravel()
    with np.errstate(divide="ignore", invalid="ignore"):
        marod = (prices - mean) / mean * 100.0
    # warm-up（mean=NaN）・0 除算（mean=0→inf）由来の非有限値は NaN に落として描画除外。
    return np.where(np.isfinite(marod), marod, np.nan)


def _rolling_causal(values: np.ndarray, window_n: int, reducer) -> np.ndarray:
    """各バー t で **当該バー t を除く** 直近 window_n 本に reducer を適用する（因果・非リペイント）。

    実体は共有プリミティブ ``common.marod_bands.rolling_causal``（SOLID 是正 🟡-10 で移設・
    数値挙動不変）。本名は既存テスト・呼出互換のため温存する。
    """
    return _bands.rolling_causal(values, window_n, reducer)


def _rolling_causal_fast(values: np.ndarray, window_n: int, kind: str, q: float | None = None) -> np.ndarray:
    """``_rolling_causal`` のベクトル化版（quantile/mean/std 限定・出力は完全一致）。

    実体は共有プリミティブ ``common.marod_bands.rolling_causal_fast``（ISSUE-154 の性能是正
    込み・SOLID 是正 🟡-10 で移設・数値挙動不変）。
    """
    return _bands.rolling_causal_fast(values, window_n, kind, q)


def marod_quantile_bands(
    marod: np.ndarray,
    *,
    window_n: int = DEFAULT_WINDOW_N,
    q_low: float = DEFAULT_Q_LOW,
    q_high: float = DEFAULT_Q_HIGH,
) -> tuple[np.ndarray, np.ndarray]:
    """MAROD 系列の因果ローリング経験分位バンド（下側 q_low・上側 q_high）を返す。

    各バー t で当該バーを除く直近 window_n 本の有限 MAROD の経験分位 q。MAROD＝乖離率×100
    ゆえ btlm_trail の経験分位バンド（乖離率の分位）と数値整合（スケール不変）。因果・非リペイント。

    Args:
        marod: MAROD 系列（``marod_series`` の出力・warm-up は NaN）。
        window_n: ローリング窓の本数（min 2）。
        q_low/q_high: 分位ペア（0 < q_low < q_high < 1）。

    Returns:
        (band_low, band_high)。各長さ n。有限本数 < 2 のバーは NaN。

    Raises:
        ValueError: window_n < 2、または分位ペア不正時。
    """
    # 実体は共有プリミティブ（SOLID 是正 🟡-10・数値挙動不変）。
    return _bands.quantile_bands(marod, window_n=window_n, q_low=q_low, q_high=q_high)


def marod_sigma_band(
    marod: np.ndarray,
    *,
    window_n: int = DEFAULT_WINDOW_N,
    mult: float = SIGMA_MULT,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """MAROD 系列の因果ローリング σ バンド（ローリング平均 ± mult·σ）を返す。

    実測（定常性検定 2026-07-20）で MAROD は平均定常・分散非定常。よって固定 σ でなく
    ローリング σ を用いる。中心はローリング平均・幅は標本標準偏差（ddof=1）× mult。
    各バー t で当該バーを除く直近 window_n 本の有限 MAROD から算出（因果・非リペイント）。

    Args:
        marod: MAROD 系列。
        window_n: ローリング窓の本数（min 2）。
        mult: σ 倍率（既定 2.0＝5%/95% 分位と対称）。

    Returns:
        (band_low, band_high, mean, std)。各長さ n。有限本数 < 2 のバーは NaN。

    Raises:
        ValueError: window_n < 2 のとき。
    """
    # 実体は共有プリミティブ（SOLID 是正 🟡-10・数値挙動不変）。
    return _bands.sigma_band(marod, window_n=window_n, mult=mult)


def _validate_window_qpair(window_n: int, q_low: float, q_high: float) -> tuple[int, float, float]:
    """window_n（>= _MIN_STAT_OBS）と分位ペア（0<q_low<q_high<1）を検証する。違反は ValueError。"""
    n = int(window_n)
    if n < _MIN_STAT_OBS:
        raise ValueError(f"window_n は {_MIN_STAT_OBS} 以上が必要です: window_n={window_n}")
    ql, qh = float(q_low), float(q_high)
    if not (0.0 < ql < qh < 1.0):
        raise ValueError(
            f"分位ペアは 0 < q_low < q_high < 1 が必要です: q_low={q_low}, q_high={q_high}"
        )
    return n, ql, qh


def marod_outlier_event_quantiles(
    marod: np.ndarray,
    *,
    window_n: int = DEFAULT_WINDOW_N,
    q_low: float = DEFAULT_Q_LOW,
    q_high: float = DEFAULT_Q_HIGH,
    q_out: float | None = DEFAULT_Q_OUT,
    k_events: int = DEFAULT_K_EVENTS,
    event_agg: str = DEFAULT_EVENT_AGG,
    bands: "tuple[np.ndarray, np.ndarray] | None" = None,
    include_all: bool = True,
) -> dict[str, np.ndarray]:
    """外れ値イベント（正常バンド超）の因果分位水準を返す（系列レベル API）。

    正常バンドを ``marod_quantile_bands``（当該バー除外の因果窓）で算出し、イベント検出・
    集計（episode/bar）・分位算出は共有プリミティブ ``common.event_quantiles.
    outlier_event_quantiles``（指標横断の正実装）へ委譲する。仕様・契約（イベント定義・
    因果境界・戻り値キー・例外）は委譲先のとおり。

    Args:
        marod: MAROD 系列（``marod_series`` 等の出力）。
        window_n: 正常バンドの因果ローリング窓（min 2）。
        q_low/q_high: 正常バンドの分位ペア（イベント判定の境界）。
        q_out: イベントの極端分位（有効条件 max(q_high, 0.5) < q_out < 1・無効は極端線のみオフ）。
        k_events: ローリング側の直近観測件数（min 1。episode ではエピソード数）。
        event_agg: 集計単位（"episode"/"bar"）。

    Returns:
        dict。キーは med_hi/ext_hi/med_lo/ext_lo（直近 k_events 件）と *_all（全履歴）。

    Raises:
        ValueError: window_n / 分位ペア不正、k_events < 1、または event_agg 不正のとき。
    """
    # 実体は共有ラッパ（バンド算出＋ event_quantiles 委譲・SOLID 是正 🟡-10・数値挙動不変）。
    return _bands.outlier_event_quantiles(
        marod, window_n=window_n, q_low=q_low, q_high=q_high,
        q_out=q_out, k_events=k_events, event_agg=event_agg,
        bands=bands, include_all=include_all,
    )
