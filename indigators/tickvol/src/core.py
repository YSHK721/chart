"""tickvol core — ティックボリューム（1 足あたりの tick 数）の系列化（純関数）。

層名/責務:
    core（純粋計算層）。OHLCV DataFrame の ``volume`` 列＝**その足の間に到来した tick 数**を
    そのまま指標系列として取り出す。窓・平滑・状態を一切持たない**点ごとの写像**である。

「ティックボリューム」の定義（この数値が何であるかの根拠）:
    供給側 :mod:`marketdata` の 1 分足原子（``jp225_tick_m1.csv``）と、そこからのロールアップ・
    resample が持つ ``volume`` 列は、当該期間に到来した tick 数の合計である。ライブの形成中バー
    （``adapter.compute.forming_bar``）も ``volume = float(len(mids))``＝窓内 tick 数で作られる
    （同 module）。すなわち確定足・形成中足のいずれでも ``volume`` は tick 数であり、本指標は
    その値を加工せずに描く。

    出来高（約定数量）ではない。CFD/指数の配信に約定数量が無いため、値動きの活況度の代理として
    tick 数を用いる（``volume`` という列名は供給側 CSV の既存名であり、意味は tick 数）。

非定義（やらないこと）:
    - 平滑・正規化・色分け（上昇/下降）を行わない。値は供給値そのままで、可逆に読める。
    - volume 列を持たないデータセット（例 ``jp225`` 日足 CSV）では KeyError を送出する。
      呼び出し境界（IndicatorComputeAdapter）が ``missing_column`` へ翻訳する。

依存: 外部 pandas のみ（描画ライブラリ・プロジェクト内他パッケージを import しない）。
"""

from __future__ import annotations

import pandas as pd

# 出力系列名。front の SeriesDef.seriesName（usecase/catalog.js）と完全一致させる（F3 照合）。
TICKVOL_COLUMN = "tickvol"

# 供給側の tick 数列の別名（大小不問で解決する）。marketdata の CSV は小文字 ``volume``。
_VOLUME_NAMES = ("volume", "vol")


def resolve_volume_column(df: pd.DataFrame) -> str:
    """tick 数列の実列名を大小不問で解決する（無ければ KeyError）。"""
    lower_map = {str(c).lower(): c for c in df.columns}
    for name in _VOLUME_NAMES:
        if name in lower_map:
            return lower_map[name]
    raise KeyError(
        "ティックボリューム列が存在しません（volume / vol のいずれかが必要）。"
    )


def build_tickvol(df: pd.DataFrame) -> pd.Series:
    """tick 数列を float 系列として返す（index は入力のまま・非数値は NaN）。

    非数値・欠損は NaN のままにする（捨てるのは出力アダプタ側＝描かない点になる）。
    リプレイの形成中バーは tick 数を持たない（web/js/replay/forming_plan.js の
    ``formingStatesAt`` が OHLC のみを送る）ため、その行は NaN になり点が立たない。
    """
    col = resolve_volume_column(df)
    return pd.to_numeric(df[col], errors="coerce").astype(float)
