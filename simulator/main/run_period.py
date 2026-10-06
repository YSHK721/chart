"""選んだ足（Period）で判定する run の組み立て（2026-10-06・依頼者承認）。

``build_interactor(period=...)`` の値は Tester Settings の Period ラベル（"M1"・"Daily" 等・
`simulator.usecase.tester_settings.enums.TIMEFRAME_INI_LABELS`）である。是正前は名前として
受け取るだけで使われず、run は常にデータの 1 分足で判定していた（Period=Daily は投入前に
「データセットと一致しません」で止まっていた）。

対応表の右辺は marketdata の時間足名（`marketdata.resample.TIMEFRAME_RULES` の鍵）であり、
足の区切りは marketdata が決める。marketdata に無い足（M2・H2 等）は推測で近い足へ寄せず
`ConfigError` で止める。
"""
from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any, Callable

from marketdata.resample import TIMEFRAME_RULES
from simulator.adapter.repository.period_bars import period_frame, write_marketdata_csv
from simulator.domain.exceptions import ConfigError
from simulator.usecase.tester_settings.enums import (
    TIMEFRAME_FROM_MINUTE_BARS,
    TIMEFRAME_INI_LABELS,
)

#: データの足（1 分足）の時間足名。選んだ足がこれなら従来どおり（まとめない）。
DATA_TF = "1m"

#: Period ラベル → marketdata の時間足名（宣言は `TIMEFRAME_FROM_MINUTE_BARS` の 1 か所）。
RUN_TF_BY_PERIOD: "dict[str, str]" = {
    TIMEFRAME_INI_LABELS[timeframe]: code
    for timeframe, code in TIMEFRAME_FROM_MINUTE_BARS.items()
}
assert set(RUN_TF_BY_PERIOD.values()) <= set(TIMEFRAME_RULES), "marketdata に無い時間足名"


def run_tf_of(period: Any) -> str:
    """Period ラベルの時間足名（marketdata に無い足は `ConfigError`）。"""
    tf = RUN_TF_BY_PERIOD.get(period)
    if tf is None:
        raise ConfigError(
            f"period {period!r} の足は 1 分足から作れません"
            f"（選べる足: {' / '.join(RUN_TF_BY_PERIOD)}）",
            context={"period": period, "supported": sorted(RUN_TF_BY_PERIOD)},
        )
    return tf


#: 観測の境界（検査側の設計・絶対命令 2026-09-25）: 選んだ足の実体を作るたびに時間足名を
#: 知らせる注入点。1 ジョブで作る回数（作った数 − 使った数 = 0）を検定する口であり、既定なし。
_build_observer: "Callable[[str], None] | None" = None


def set_build_observer(observer: "Callable[[str], None] | None") -> None:
    global _build_observer
    _build_observer = observer


class PeriodDataset:
    """1 分足から作った ``tf`` の足を、marketdata 形式の一時 CSV として差し出す。

    EA 束縛は「データ実体のパス」から指標とバーを読む（`ea_bindings.sources`）。選んだ足の
    実体を同じ形で渡すので、束縛は 1 行も変えずに選んだ足の指標を作る。読み終えたら
    `close` で消す（途中で例外が出ても一時ディレクトリは回収時に消える）。
    """

    def __init__(self, m1_bars: Any, tf: str) -> None:
        if _build_observer is not None:
            _build_observer(tf)
        self._tmp = tempfile.TemporaryDirectory(prefix="sim_period_")
        self.path = write_marketdata_csv(
            period_frame(m1_bars, tf), Path(self._tmp.name) / f"period_{tf}.csv"
        )

    def close(self) -> None:
        self._tmp.cleanup()
