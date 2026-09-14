"""層名: core 層（純粋計算）。

責務:
    PRO!fitRSI の iRSI（Wilder Relative Strength Index）算出を numpy 配列のみで行う
    純粋関数層。入出力・描画・pandas を含まない。iRSI は共有 mql_builtins へ集約済み
    （import 再公開で in-package 参照面を維持）。適用価格の選択は共有 common を再利用する。
    水準（正常帯・外れ値）は :mod:`src.levels` が担う（本モジュールは持たない）。

含む構造:
    compute_rsi        : 昇順 価格系列から iRSI 系列（warm-up 0）を算出（純粋関数）。
    APPLY_TO_PRICE     : 独自 Apply 値 → common.AppliedPrice の写像（core 入口の定数）。
    compute_rsi_full   : OHLC ＋ apply を入口に iRSI を算出した frozen DTO。
    RsiResult          : 計算成果の不変 DTO（rsi writeable=False）。

元 MQL 対応（``PRO!fitRSI.mq4`` ＋ 標準 ``iRSI``（``RSI.mq5``）を昇順=古→新へ 1:1 変換）:
    iRSI(period, applied) → compute_rsi。diff=price[i]-price[i-1]。
        seed（i==period）: pos/neg = period 本の up/down 平均。
        main（i>period）  : Wilder 平滑 pos[i]=(pos[i-1]*(period-1)+up)/period。
        RSI: neg!=0 → 100-100/(1+pos/neg); neg==0&pos!=0 → 100; neg==0&pos==0 → 50。
        warm-up（i<period）は 0（元 iRSI/SetIndexDrawBegin 既定）。
        rates_total<=period は全 0（元 RSI.mq5 の早期 return）。
    Apply（独自 input） → APPLY_TO_PRICE で common.AppliedPrice へ写像し
        applied_price(kind, o,h,l,c) で価格系列を選択。既定 Apply=5 → TYPICAL。
    ※ 元の σ 7 水準（`iStdDevOnArray` / `iMAOnArray(MODE_SMA)` による全系列 avg±1/2/3σ ＋
      固定 50）は本移植では持たない。**全系列＝未来のバーを含む非因果な水準**であり、ライブ
      表示の水準として成立しないためである。因果ローリング分位＋POT/GPD による
      :mod:`src.levels` へ全面置換した（ユーザー承認 2026-08-02・SPEC §5.4）。
    ※ 元 `iMAOnArray(EMA, InpMAPeriod=5)` による RSI の EMA 平滑線（ExtMABuffer）も
      持たない（`ma_period` 設定項目ごと削除・ユーザー承認 2026-08-02）。

依存（PORTING_GUIDE §8）:
    標準: __future__, dataclasses, sys, pathlib / 外部: numpy
    共有: common（applied_price, AppliedPrice）。
    pandas/描画 import は禁止。
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

# 共有ライブラリ mql_builtins（indigators/ 直下）を絶対 import で再利用する。
from mql_builtins import compute_rsi  # noqa: F401  # 正準 iRSI（再公開して in-package 参照面を維持）

from common import AppliedPrice, applied_price

# 元 input の既定値（PRO!fitRSI.mq4: InpRSIPeriod=6, Apply=5）。
DEFAULT_RSI_PERIOD: int = 6
DEFAULT_APPLY: int = 5  # 5 -> PRICE_TYPICAL（元 input 既定）

# 独自 Apply 値 -> common.AppliedPrice の写像（PRO!fitRSI.mq4 独自仕様）。
# 1:OPEN, 2:HIGH, 3:LOW, 4:MEDIAN, 5:TYPICAL, 6:WEIGHTED, それ以外:CLOSE。
_APPLY_MAP: dict[int, AppliedPrice] = {
    1: AppliedPrice.OPEN,
    2: AppliedPrice.HIGH,
    3: AppliedPrice.LOW,
    4: AppliedPrice.MEDIAN,
    5: AppliedPrice.TYPICAL,
    6: AppliedPrice.WEIGHTED,
}


def APPLY_TO_PRICE(apply: int) -> AppliedPrice:
    """独自 Apply 値を common.AppliedPrice へ写像する（既定外は CLOSE）。"""
    return _APPLY_MAP.get(apply, AppliedPrice.CLOSE)


# compute_rsi（iRSI Wilder）は共有 mql_builtins へ集約済み（上部で import・再公開）。
# 既定 period 定数 DEFAULT_RSI_PERIOD は本パッケージに残置し、呼び出しで period= 明示する。


@dataclass(frozen=True)
class RsiResult:
    """PRO!fitRSI の計算成果（数値のみ・描画非依存の不変 DTO）。

    Attributes:
        rsi: iRSI 系列（warm-up 0。writeable=False）。
    """

    rsi: np.ndarray

    def __post_init__(self) -> None:
        rsi = np.asarray(self.rsi, dtype=np.float64)
        rsi.setflags(write=False)  # DTO は不変（profit_mfi/profit_stc 準拠）
        object.__setattr__(self, "rsi", rsi)


def compute_rsi_full(
    open_: np.ndarray,
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    *,
    rsi_period: int = DEFAULT_RSI_PERIOD,
    apply: int = DEFAULT_APPLY,
) -> RsiResult:
    """OHLC ＋ apply から iRSI を算出し RsiResult を返す。

    ``apply`` を ``APPLY_TO_PRICE`` で common.AppliedPrice へ写像し、共有
    ``applied_price`` で価格系列を選択したうえで ``compute_rsi`` を呼ぶ。

    Args:
        open_/high/low/close: 昇順（古→新）の OHLC 配列（同長）。
        rsi_period: RSI 期間（既定 6）。
        apply: 適用価格選択（既定 5 -> TYPICAL。それ以外 -> CLOSE）。

    Returns:
        RsiResult（rsi）。

    Raises:
        ValueError: OHLC 長不一致、または ``rsi_period < 2``（compute_rsi 経由）。
    """
    open_ = np.asarray(open_, dtype=np.float64)
    high = np.asarray(high, dtype=np.float64)
    low = np.asarray(low, dtype=np.float64)
    close = np.asarray(close, dtype=np.float64)
    if not (open_.shape == high.shape == low.shape == close.shape):
        raise ValueError(
            f"OHLC の長さが一致しません: "
            f"{open_.shape}/{high.shape}/{low.shape}/{close.shape}"
        )

    kind = APPLY_TO_PRICE(apply)
    price = applied_price(kind, open_, high, low, close)
    return RsiResult(rsi=compute_rsi(price, period=rsi_period))
