"""tickvol_updown core — 上昇／下落ティック数の n 区間累積（純関数）。

①層名/責務:
    core（純粋計算層・numpy/pandas のみ）。各足の **上昇ティック数 up** と **下落ティック数 dn**
    を、直近 ``window_n`` 本ぶん合計し、その差（上昇 − 下落）を 1 本のバーの値として返す。

②差で描く（依頼者指定 2026-08-02）ことの実測上の意味:
    上昇の累積と下落の累積はほぼ同じ大きさで動くため（移動累積の相関 0.9993〜0.9999）、2 本の
    バーはほぼ鏡像になり読み取れない。差にすると非対称だけが残り、どちら側が優勢かは読める。
    ただしその差自体はコイン投げ以下のばらつきしか持たない（``Var(up-dn)/E[tick数] = 0.83〜0.97``・
    公平コイン=1.0）。**差の大きさに予測情報は無い**ことは実測済みで、採用は依頼者裁定（ISSUE-241）。

③データ要件:
    ``up`` / ``dn`` 列は **ティック由来のデータセットだけが持つ任意列**（marketdata.csv_schema）。
    列が無いデータセット（1 分足 OHLC 由来の jp225_m1 / jp225_daily / sample）では
    :class:`KeyError` を送出し、呼出境界が ``missing_column`` へ翻訳する。

④依存: 外部 numpy / pandas のみ（描画ライブラリ・他指標パッケージを import しない）。
"""

from __future__ import annotations

import numpy as np
import pandas as pd

#: 出力系列名（front の SeriesDef.seriesName と完全一致させる）。
#: 1 本のバーで描く（依頼者指定 2026-08-02）。値は上昇と下落の差で、符号が優勢な側を表す。
NET_SERIES = "tickvol_updown"

#: 供給側の列名（marketdata.csv_schema.UPDOWN_COLUMNS と同値。大小不問で解決する）。
_UP_NAMES = ("up",)
_DN_NAMES = ("dn", "down")

#: 累積区間の既定。動的（パラメータ）であり、ここは UI の初期値にすぎない。
DEFAULT_WINDOW_N: int = 20


def _resolve(df: pd.DataFrame, names: "tuple[str, ...]", label: str) -> str:
    lower_map = {str(c).lower(): c for c in df.columns}
    for name in names:
        if name in lower_map:
            return lower_map[name]
    raise KeyError(
        f"{label}のティック数列が存在しません（{' / '.join(names)} のいずれかが必要）。"
        "ティック由来のデータセット（jp225_tick）でのみ利用できます。"
    )


def resolve_updown_columns(df: pd.DataFrame) -> "tuple[str, str]":
    """上昇／下落ティック数の実列名を大小不問で解決する（無ければ KeyError）。"""
    return _resolve(df, _UP_NAMES, "上昇"), _resolve(df, _DN_NAMES, "下落")


def net_updown(df: pd.DataFrame, *, window_n: int = DEFAULT_WINDOW_N) -> np.ndarray:
    """直近 ``window_n`` 本の「上昇ティック数 − 下落ティック数」を返す（1 本のバーの値）。

    正なら上昇ティックが優勢、負なら下落ティックが優勢。窓が満たない先頭区間は NaN。
    """
    up_sum, dn_sum = cumulative_updown(df, window_n=window_n)
    return up_sum - dn_sum


def cumulative_updown(
    df: pd.DataFrame, *, window_n: int = DEFAULT_WINDOW_N
) -> "tuple[np.ndarray, np.ndarray]":
    """直近 ``window_n`` 本の上昇／下落ティック数の合計を返す。

    当該バーを **含む** 窓（``[t-window_n+1, t]``）。形成中バーの値が増えれば当日ぶんも増える
    （tickvol 本体と同じ「その足の tick 数」の意味を保つ）。窓が満たない先頭区間は NaN。

    Returns:
        ``(up_sum, dn_sum)``。各長さ n。

    Raises:
        KeyError: up / dn 列が無い場合。
        ValueError: ``window_n < 1`` の場合。
    """
    n = int(window_n)
    if n < 1:
        raise ValueError(f"window_n は 1 以上が必要です: window_n={window_n}")
    up_col, dn_col = resolve_updown_columns(df)
    up = pd.to_numeric(df[up_col], errors="coerce").astype(float)
    dn = pd.to_numeric(df[dn_col], errors="coerce").astype(float)
    return (
        up.rolling(n).sum().to_numpy(),
        dn.rolling(n).sum().to_numpy(),
    )
