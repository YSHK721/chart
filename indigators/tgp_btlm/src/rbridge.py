"""R tgp バックエンド: rpy2 経由で ``tgp::btlm`` を呼び出す BtlmFitter 実装。

層名/責務:
    インフラアダプタ（偶有的性質の隔離先）。R / tgp / rpy2 という外部技術依存を
    本ファイルにのみ閉じ込める。core/bands/plot/lwc/tests はこれを import しない。

含む構造:
    TgpBtlmFitter : BtlmFitter ポートの R 実装。

元 MQL4 の対応:
    ``RInit`` / ``RExecute("require(tgp)")`` /
    ``model <- btlm(X=X, Z=hist, verb=0, BTE=c(2000,15000,2), R=1)`` /
    ``model$Zp.mean | Zp.q1 | Zp.q2`` の一連。元コードの非同期実行（RExecuteAsync /
    RIsBusy）は MT4 のティック駆動を待たないための足場であり、バッチ移植では同期実行とする。

実行要件（このバックエンド利用時のみ）:
    R 本体 + R パッケージ ``tgp`` + Python パッケージ ``rpy2`` が必要。
    未導入環境では本ファイルを import しても失敗しない（rpy2 は遅延 import）が、
    ``fit_predict`` 呼び出し時に明示的な例外を送出する。

分位点について:
    tgp の btlm はネイティブに Zp.q1=5% / Zp.q2=95% を返す。要求分位点が 5/95% 以外の
    場合は、予測分布を正規近似（平均=Zp.mean、標準偏差=(q95-q5)/(2·z_0.95)）し
    ``core.norm_ppf`` で再構成する（ガイド: 非既定分位点は近似である旨を明示）。

依存:
    標準: __future__ / 外部: numpy, rpy2(遅延) / プロジェクト内: core
"""

from __future__ import annotations

import numpy as np

from .core import (
    DEFAULT_BTE,
    DEFAULT_Q_HIGH,
    DEFAULT_Q_LOW,
    DEFAULT_R,
    BtlmResult,
    norm_ppf,
)

# 正規近似の基準（ネイティブ 5/95% の z 値）。
_Z95 = float(norm_ppf(0.95))  # ≒ 1.6448536
_NATIVE_LOW, _NATIVE_HIGH = 0.05, 0.95


class TgpBtlmFitter:
    """``tgp::btlm`` を rpy2 経由で呼ぶ BtlmFitter 実装。

    Args:
        bte: btlm の BTE=(Burn-in, Total, Every)。既定 (2000, 15000, 2)。
        r_restarts: btlm の R 引数（リスタート回数）。既定 1。
        seed: R 側乱数シード（再現性確保。None なら設定しない）。
    """

    def __init__(
        self,
        *,
        bte: tuple[int, int, int] = DEFAULT_BTE,
        r_restarts: int = DEFAULT_R,
        seed: int | None = None,
    ) -> None:
        self.bte = bte
        self.r_restarts = r_restarts
        self.seed = seed

    def fit_predict(
        self,
        x: np.ndarray,
        z: np.ndarray,
        *,
        q_low: float = DEFAULT_Q_LOW,
        q_high: float = DEFAULT_Q_HIGH,
    ) -> BtlmResult:
        """tgp::btlm を実行し予測平均・上下分位点を返す。

        Raises:
            ImportError: rpy2 が未導入の場合。
            RuntimeError: R パッケージ tgp のロードに失敗した場合。
        """
        try:
            import rpy2.robjects as ro
            from rpy2.robjects.packages import importr
        except ImportError as exc:  # pragma: no cover - 環境依存
            raise ImportError(
                "TgpBtlmFitter には rpy2 が必要です（pip install rpy2、および R 本体）。"
            ) from exc

        try:
            tgp = importr("tgp")
        except Exception as exc:  # pragma: no cover - 環境依存
            raise RuntimeError(
                "R パッケージ 'tgp' をロードできません。R で install.packages('tgp') を実行してください。"
            ) from exc

        # 入力は FloatVector/IntVector で明示的に R へ渡し、出力は ListVector を rx2 で取り出して
        # np.asarray する。numpy2ri の global activate/deactivate（rpy2>=3.6 で削除・例外化）には
        # 依存しない（converter 非依存）。
        if self.seed is not None:
            ro.r("set.seed")(self.seed)

        x_r = ro.FloatVector(np.asarray(x, dtype=np.float64))
        z_r = ro.FloatVector(np.asarray(z, dtype=np.float64))
        bte_r = ro.IntVector(self.bte)

        model = tgp.btlm(
            X=x_r, Z=z_r, BTE=bte_r, R=self.r_restarts, verb=0,
            **{"pred.n": True},
        )

        mean = np.asarray(model.rx2("Zp.mean"), dtype=np.float64).ravel()
        nat_low = np.asarray(model.rx2("Zp.q1"), dtype=np.float64).ravel()
        nat_high = np.asarray(model.rx2("Zp.q2"), dtype=np.float64).ravel()

        if (q_low, q_high) == (_NATIVE_LOW, _NATIVE_HIGH):
            return BtlmResult(mean=mean, q_low=nat_low, q_high=nat_high)

        # 非既定分位点: ネイティブ 90% 帯から正規近似して再構成。
        sigma = (nat_high - nat_low) / (2.0 * _Z95)
        return BtlmResult(
            mean=mean,
            q_low=mean + norm_ppf(q_low) * sigma,
            q_high=mean + norm_ppf(q_high) * sigma,
        )
