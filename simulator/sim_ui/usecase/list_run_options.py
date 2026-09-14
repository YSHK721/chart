"""U-ListRunOptions: 実行指示フォームの選択肢を束ねる薄い query UC（usecase・Phase 6 拡張）。

RunOptionsPort の datasets()/ea_names() を 1 つの DTO（:class:`RunOptions`）へ束ねるだけ。
規則は持たない（フォームが「何を選べるか」を 1 回で得るための入口）。投入経路とは無関係
（SubmitJobInteractor には足さない・既存 backtest verbatim 契約 byte 不変）。
"""
from __future__ import annotations

from dataclasses import dataclass

from simulator.sim_ui.usecase.run_options_ports import RunOptionsPort, RunProfile


@dataclass(frozen=True)
class RunOptions:
    """実行指示フォームの選択肢（データセット一覧＋ea_name 一覧）。"""

    datasets: "list[RunProfile]"
    ea_names: "list[str]"


class ListRunOptionsInteractor:
    """RunOptionsPort から選択肢を 1 回で取得する query UC。"""

    def __init__(self, *, port: RunOptionsPort) -> None:
        self._port = port

    def list(self) -> RunOptions:
        return RunOptions(datasets=self._port.datasets(), ea_names=self._port.ea_names())
