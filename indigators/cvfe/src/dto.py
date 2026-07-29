"""CVFE のデータ転送オブジェクトとパラメータ（仕様 §3.1・§3.2）。

層名/責務:
    純粋なデータ定義層。計算を持たない（唯一の例外は :class:`CvfeParams` の
    §3.1 制約検証であり、これは「値の妥当性」というデータ自身の不変条件である）。

すべて ``dataclass(frozen=True)``。配列を保持する :class:`CvfeResult` は
生成時に ``setflags(write=False)`` を適用する（仕様 §4.8）。

依存: 標準 dataclasses / 外部 numpy / プロジェクト内 errors。
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from .errors import E05_PARAM_RANGE, CvfeError

# 仕様 §4.5：HAR の階層（日・週 5 本・月 22 本）と説明変数の本数。
HAR_LAG_WEEK: int = 5
HAR_LAG_MONTH: int = 22
HAR_N_COEF: int = 6

#: 仕様 §3.1 の ``N >= n_har + 22`` に現れる先読み本数（= HAR_LAG_MONTH）。
WARMUP_LAGS: int = HAR_LAG_MONTH


@dataclass(frozen=True)
class CvfeParams:
    """仕様 §3.1 の入力パラメータ。生成時に §3.1 の制約を検証する（E05）。

    ``freeze_thresh`` は仕様 §3.1 が値域を規定していないため検証しない
    （仕様に無い制約を実装が追加しない。閾値の根拠自体は仕様 §10 TBD-2）。
    """

    bar_interval_sec: int
    n_har: int = 1500
    lam_gap: float = 0.97
    jump_alpha: float = 0.999
    freeze_thresh: float = 0.05
    refit_every: int = 0

    def __post_init__(self) -> None:
        if not isinstance(self.bar_interval_sec, (int, np.integer)) or self.bar_interval_sec < 60:
            raise CvfeError(E05_PARAM_RANGE, f"bar_interval_sec >= 60 が必要: {self.bar_interval_sec!r}")
        if not isinstance(self.n_har, (int, np.integer)) or self.n_har < 500:
            raise CvfeError(E05_PARAM_RANGE, f"n_har >= 500 が必要: {self.n_har!r}")
        if not (0.90 <= float(self.lam_gap) < 1.0):
            raise CvfeError(E05_PARAM_RANGE, f"0.90 <= lam_gap < 1.0 が必要: {self.lam_gap!r}")
        if not (0.99 <= float(self.jump_alpha) <= 0.9999):
            raise CvfeError(E05_PARAM_RANGE, f"0.99 <= jump_alpha <= 0.9999 が必要: {self.jump_alpha!r}")
        if not isinstance(self.refit_every, (int, np.integer)) or self.refit_every < 0:
            raise CvfeError(E05_PARAM_RANGE, f"refit_every >= 0 が必要: {self.refit_every!r}")

    @property
    def first_available_index(self) -> int:
        """予測を開始できる最初のバー番号 ``t0``。

        仕様 §4.5-3 の学習標本 ``t ∈ [t0 − n_har − 1, t0 − 2]`` が非負であり、
        かつその先頭バーの説明変数が 22 本の遡及を満たすには ``t0 >= n_har + 22``。
        仕様 §3.1 の ``N >= n_har + 22`` と同じ本数である。
        """
        return int(self.n_har) + WARMUP_LAGS


@dataclass(frozen=True)
class BarMeasure:
    """1 バーの確定量（仕様 §4.3・§4.4 の出力）。

    一括経路と逐次経路の唯一の接続面。両経路はこの型のみを介して接続され、
    同一の :func:`~.engine.measure_bar` が生成する（仕様 §6 bit 一致の構造的根拠）。
    """

    index: int
    n: int
    v: float
    c: float
    j: float
    jump_flag: bool
    p_open: float
    p_close: float
    p_high: float
    p_low: float
    t_first: float
    t_last: float
    edge_start: float
    valid: bool


@dataclass(frozen=True)
class QualityReport:
    """仕様 §4.1・§4.2 の診断結果。"""

    rv_mean: dict[int, float]
    omega2_hat: float
    freeze_ratio: float
    signature_slope: float
    quality_gate: str
    measure_id: str
    delta_star_sec: int


@dataclass(frozen=True)
class CvfeState:
    """段階 0・1 と段階 4（学習）の成果。逐次経路の初期化に必要十分な情報。"""

    quality: QualityReport
    har_coef: np.ndarray
    har_resid_var: float
    gap_v_init: float
    first_available_index: int

    @property
    def measure_id(self) -> str:
        return self.quality.measure_id

    @property
    def delta_star_sec(self) -> int:
        return self.quality.delta_star_sec


@dataclass(frozen=True)
class CvfeResult:
    """仕様 §3.2 の出力 DTO。配列は書込不可（不変）。"""

    sigma_hat: np.ndarray
    sigma_oc: np.ndarray
    sigma_co: np.ndarray
    measure_id: str
    delta_star_sec: int
    omega2_hat: float
    freeze_ratio: float
    jump_flag: np.ndarray
    har_coef: np.ndarray
    har_resid_var: float
    quality_gate: str
    available: np.ndarray
    signature_slope: float = field(default=float("nan"))

    def __post_init__(self) -> None:
        for name in ("sigma_hat", "sigma_oc", "sigma_co", "jump_flag", "har_coef", "available"):
            arr = getattr(self, name)
            arr.setflags(write=False)
