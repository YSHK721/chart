"""BTLM 指標のコア（純粋ロジック・外部I/O非依存・numpy のみ）。

層名/責務:
    core 層。btlm（Bayesian Treed Linear Model）回帰の「概念」を表す純粋部品を提供する。
    実際のモデル当てはめ（R tgp）は外部技術＝偶有的性質であり、本層は import せず、
    ``BtlmFitter`` ポート（Protocol）として境界の外へ追い出す。

含む構造:
    * 既定パラメータ定数（maxbars / 分位点 / BTE）
    * BtlmResult         : 予測平均・上下分位点を保持する不変 DTO
    * BtlmFitter         : モデル当てはめのポート（Protocol）
    * make_design        : 系列 → (X=1..n, Z) の説明変数設計
    * mean_column / quantile_column : 成果物列名の単一定義
    * norm_ppf           : 標準正規の逆累積分布（実体は共有 ``common.normal_dist``・再エクスポート）

元 MQL4 の対応:
    ``OnCalculate`` 内の ``X <- seq(1,maxbars)`` / ``Z=rev(hist)`` /
    ``btlm(X,Z,BTE,R)`` / ``model$Zp.mean|q1|q2`` に対応する計算概念。
    非同期 R 連携（RIsBusy/RExecuteAsync）は描画足場でありここには持ち込まない。

依存:
    標準: dataclasses, typing / 外部: numpy / プロジェクト内: common.normal_dist
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

import numpy as np

# Acklam 有理近似の実体は共有プリミティブへ 1 本化した（ISSUE-179 項目 3）。
# 本モジュールの公開面（``src/__init__.py`` の ``__all__``）を保つため同名で再エクスポートする。
from common.normal_dist import norm_ppf

# 既定値（元 MQL4 の input / btlm 引数に一致）。
DEFAULT_MAXBARS: int = 100          # extern int maxbars = 100;
DEFAULT_Q_LOW: float = 0.05         # tgp 既定 Zp.q1 = 5%
DEFAULT_Q_HIGH: float = 0.95        # tgp 既定 Zp.q2 = 95%
DEFAULT_BTE: tuple[int, int, int] = (2000, 15000, 2)  # btlm(..., BTE=c(2000,15000,2))
DEFAULT_R: int = 1                  # btlm(..., R=1)

# 成果物列の系統名（単一定義。bands と出力アダプタが共有する）。
_MEAN_COLUMN = "btlm_mean"


def mean_column() -> str:
    """予測平均の成果物列名を返す。"""
    return _MEAN_COLUMN


def quantile_column(q: float) -> str:
    """分位点 q（0..1）に対応する成果物列名（例 0.05 -> 'btlm_q5'）を返す。"""
    return f"btlm_q{int(round(q * 100))}"


@dataclass(frozen=True)
class BtlmResult:
    """btlm 予測の成果（予測位置と同順・同長の配列）。

    Attributes:
        mean: 予測平均（model$Zp.mean 相当）。
        q_low: 下側予測分位点（既定で 5% = model$Zp.q1 相当）。
        q_high: 上側予測分位点（既定で 95% = model$Zp.q2 相当）。
    """

    mean: np.ndarray
    q_low: np.ndarray
    q_high: np.ndarray

    def __post_init__(self) -> None:
        arrays = {}
        for name in ("mean", "q_low", "q_high"):
            arr = np.asarray(getattr(self, name), dtype=np.float64)
            arr.setflags(write=False)  # DTO は不変（ガイド §2）
            object.__setattr__(self, name, arr)
            arrays[name] = arr
        sizes = {a.size for a in arrays.values()}
        if len(sizes) != 1:
            raise ValueError(
                f"mean/q_low/q_high の長さが不一致です: "
                f"{[arrays[n].size for n in ('mean', 'q_low', 'q_high')]}"
            )


@runtime_checkable
class BtlmFitter(Protocol):
    """モデル当てはめのポート（境界インターフェース）。

    実装は ``fit_predict`` を提供すればよい（R tgp バックエンド / numpy 参照実装 /
    テスト用 Fake のいずれも差し替え可能）。
    """

    def fit_predict(
        self,
        x: np.ndarray,
        z: np.ndarray,
        *,
        q_low: float = DEFAULT_Q_LOW,
        q_high: float = DEFAULT_Q_HIGH,
    ) -> BtlmResult:
        """説明変数 x と目的変数 z から予測平均・上下分位点を返す。"""
        ...


def make_design(z_series: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """目的系列から btlm の説明変数設計 (X=1..n, Z) を作る。

    元 MQL4 の ``X <- seq(1, maxbars, length=maxbars)`` / ``Z = rev(hist)`` に対応。
    Python は時系列昇順（古い→新しい）で扱うため rev は不要（ガイド §4.3）。

    Args:
        z_series: 目的変数（例: 直近 maxbars 本の Open 価格・昇順）。

    Returns:
        (x, z): x は 1..n の float 配列、z は入力をそのまま float 化した配列。

    Raises:
        ValueError: 入力が空の場合。
    """
    z = np.asarray(z_series, dtype=np.float64)
    if z.size == 0:
        raise ValueError("目的系列が空です。")
    x = np.arange(1, z.size + 1, dtype=np.float64)
    return x, z
