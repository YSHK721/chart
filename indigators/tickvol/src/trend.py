"""tickvol trend — ティックボリュームの回帰トレンドと帯（btlm_trail 仕様の参照拡張）。

①層名/責務:
    core（純粋計算層）。tick 数系列に **btlm_trail の仕様をそのまま適用**する。すなわち各バーで
    直近 ``maxbars`` 本に OLS を当て、窓末尾の値（トレンド現在位置 mean）・傾き β・残差 σ、
    その周りの帯（名目 ols／経験分位）・外れ値分位線・バンド内実績率を得る。

②実装は参照実装への委譲のみ（計算式を 1 行も写さない）:
    :func:`btlm_trail.build_btlm_trail`（F-01/F-05/F-06/F-08 の全体）と
    :func:`btlm_trail.rolling_coverage`（F-09）へ委譲する。btlm_trail 本体は無改変
    （基本設計主体・主機能無改変の参照拡張＝OCP。btlm_trail_marod / ma_marod と同じ規律）。

    ``build_btlm_trail`` はソース合成（8 択）を経る契約なので、tick 数を 4 値すべてに置いた
    合成 DataFrame を渡し ``source="close"`` を指定する。tick 数は 1 本の系列であり
    高値/安値/始値の区別を持たないため、この写像で情報は落ちない。

③既定のバンド方式が btlm_trail 本体（名目 ols）と違う理由（実測 2026-08-01・jp225_tick 6,000 本）:
    トレンドからの乖離率は右に強く裾を引く（歪度 5m +35.5 / 15m +4.35 / 1h +2.15）。tick 数は
    最小 1 の計数量で対数正規に近く、正規仮定の名目 ols バンドは成立しない。

    | 時間足 | 名目 | ols 実績率 | 経験分位 実績率 | ols で下端<1 | 経験分位で下端<1 |
    |---|---|---|---|---|---|
    | 5m  | 80% | 75.6%(-4.4pp) | 79.2%(-0.8pp) | 20.6% | 1.1% |
    | 15m | 80% | 80.8%(+0.8pp) | 79.6%(-0.4pp) | 14.8% | 0.0% |
    | 1h  | 80% | 83.2%(+3.2pp) | 80.0%(+0.0pp) | 22.6% | 0.0% |
    | 5m  | 90% | 86.0%(-4.0pp) | 89.2%(-0.8pp) | 33.8% | 4.8% |
    | 1h  | 90% | 91.6%(+1.6pp) | 89.2%(-0.8pp) | 57.5% | 0.0% |

    経験分位は全条件で名目に近く（乖離 -0.8〜0.0pp）、かつ帯下端が「tick 数として成立しない
    値（1 未満）」になる割合がほぼ 0 である。ols は最大 57.5% のバーで下端が 1 を割る。
    よって **既定は経験分位**とする（btlm_trail 本体の既定は変更しない）。

④依存: 外部 numpy / pandas、指標 :mod:`btlm_trail`（公開 API のみ）。描画ライブラリは import しない。
"""

from __future__ import annotations

import numpy as np
import pandas as pd

#: 回帰窓・経験分位の参照本数・実績率の窓は btlm_trail の既定をそのまま使う（単一情報源）。
from btlm_trail.src.core import DEFAULT_MAXBARS, DEFAULT_N_COV  # noqa: E402
from btlm_trail.src.core import DEFAULT_EMP_N  # noqa: E402
from btlm_trail.src.trail import build_btlm_trail, rolling_coverage  # noqa: E402

#: バンド方式の既定。btlm_trail 本体（"ols"）と違えている根拠は③の実測。
DEFAULT_BAND_METHOD: str = "empirical"
#: 選択肢（btlm_trail と同一語彙）。
BAND_METHODS: tuple[str, ...] = ("ols", "empirical")

#: トレンド成果のキー（表示層はこの名前で系列を組む）。
TREND_KEYS: tuple[str, ...] = (
    "mean", "band_low", "band_high", "off_low", "off_high",
    "beta", "sigma", "band_hit_rate",
)


def _as_source_frame(values) -> pd.DataFrame:
    """tick 数系列を ``build_btlm_trail`` が受ける OHLC 形へ写す（4 値とも同値）。

    tick 数は 1 本の系列であり高値/安値/始値の区別を持たない。``source="close"`` で読ませる。
    """
    v = np.asarray(values, dtype=np.float64).ravel()
    return pd.DataFrame({"open": v, "high": v, "low": v, "close": v})


def tickvol_trend(
    values,
    *,
    maxbars: int = DEFAULT_MAXBARS,
    q_low: float,
    q_high: float,
    band_method: str = DEFAULT_BAND_METHOD,
    empirical_n: int = DEFAULT_EMP_N,
    q_out: "float | None" = None,
    n_cov: int = DEFAULT_N_COV,
    with_metrics: bool = True,
) -> "dict[str, np.ndarray | None]":
    """tick 数系列の回帰トレンド・帯・外れ値分位線・メトリクスを返す。

    Args:
        values: tick 数系列（昇順）。
        maxbars: 回帰窓の本数（btlm_trail F-01）。
        q_low / q_high: 帯の分位ペア（``0 < q_low < q_high < 1``）。
        band_method: ``"ols"``（名目）／``"empirical"``（経験分位・既定）。
        empirical_n: 経験分位の参照本数（``band_method="empirical"`` のみ有効）。
        q_out: 外れ値分位（``q_high < q_out < 1`` のみ有効・無効は線なし）。
        n_cov: バンド内実績率のローリング本数。
        with_metrics: β/σ/実績率を計算するか（False なら該当キーは ``None``）。

    Returns:
        :data:`TREND_KEYS` をキーに持つ dict。各値は長さ n の配列、または ``None``
        （``off_*`` は q_out 無効時、メトリクスは ``with_metrics=False`` 時）。

    Raises:
        ValueError: 分位ペア・``band_method``・空系列が不正なとき（btlm_trail と同一規約）。
    """
    v = np.asarray(values, dtype=np.float64).ravel()
    res = build_btlm_trail(
        _as_source_frame(v), source="close", maxbars=int(maxbars),
        q_low=float(q_low), q_high=float(q_high), band_method=str(band_method),
        empirical_n=int(empirical_n), q_out=q_out,
    )
    out: "dict[str, np.ndarray | None]" = {
        "mean": res.mean,
        "band_low": res.band_low,
        "band_high": res.band_high,
        "off_low": res.off_low,
        "off_high": res.off_high,
        "beta": res.beta if with_metrics else None,
        "sigma": res.sigma if with_metrics else None,
        "band_hit_rate": (
            rolling_coverage(v, res.band_low, res.band_high, int(n_cov))
            if with_metrics else None
        ),
    }
    return out
