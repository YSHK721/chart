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
    プロジェクト内: btlm_trail/src（動的ロード。内部で common.applied_price を絶対 import）
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import numpy as np

# 既定パラメータ（btlm_trail core DEFAULT_MAXBARS と同値・8 択ソース既定 close）。
DEFAULT_SOURCE: str = "close"
DEFAULT_MAXBARS: int = 100

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
