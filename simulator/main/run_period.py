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
from typing import Any

from marketdata.resample import TIMEFRAME_RULES
from simulator.adapter.repository.period_bars import period_frame, write_marketdata_csv
from simulator.domain.exceptions import ConfigError

#: データの足（1 分足）の時間足名。選んだ足がこれなら従来どおり（まとめない）。
DATA_TF = "1m"

#: Period ラベル → marketdata の時間足名。
RUN_TF_BY_PERIOD: "dict[str, str]" = {
    "M1": "1m",
    "M5": "5m",
    "M15": "15m",
    "M30": "30m",
    "H1": "1h",
    "H4": "4h",
    "Daily": "1D",
    "Weekly": "1W",
    "Monthly": "1M",
}
assert set(RUN_TF_BY_PERIOD.values()) <= set(TIMEFRAME_RULES), "marketdata に無い時間足名"


def run_tf_of(period: Any) -> str:
    """Period ラベルの時間足名（marketdata に無い足は `ConfigError`）。"""
    tf = RUN_TF_BY_PERIOD.get(period)
    if tf is None:
        raise ConfigError(
            f"この Period では実行できません（1 分足から作れる足ではありません）: {period!r}",
            context={"period": period, "supported": sorted(RUN_TF_BY_PERIOD)},
        )
    return tf


class PeriodDataset:
    """1 分足から作った ``tf`` の足を、marketdata 形式の一時 CSV として差し出す。

    EA 束縛は「データ実体のパス」から指標とバーを読む（`ea_bindings.sources`）。選んだ足の
    実体を同じ形で渡すので、束縛は 1 行も変えずに選んだ足の指標を作る。読み終えたら
    `close` で消す（途中で例外が出ても一時ディレクトリは回収時に消える）。
    """

    def __init__(self, m1_bars: Any, tf: str) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="sim_period_")
        self.path = write_marketdata_csv(
            period_frame(m1_bars, tf), Path(self._tmp.name) / f"period_{tf}.csv"
        )

    def close(self) -> None:
        self._tmp.cleanup()
