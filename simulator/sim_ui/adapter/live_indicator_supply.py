"""案 ii の系列 → 指標レジストリ供給（adapter 層・Phase 3 F-5）。

基本設計書 §3.5.4 の案 ii（全期間を 1 回 full 計算し、バー i の値を配列から引く）を、
バックテストエンジンが要する `IndicatorPort` の形へ変換する。

規則:
    1. 系列は `CausalSeriesProbePort` の**束**で受け取る（1 指標 1 回の計算）。
    2. 整列は usecase の `align_series_to_bars` に委ねる（規則の写しをここに持たない）。
    3. **レジストリへ載せるのは台帳が選択可能とした系列だけ**（裁定 A: 選択可否の単位は
       系列）。検定を通っていない系列を混ぜると、戦略がその値を掴んだまま完走する。
    4. 系列名の衝突は明示エラー（`SeriesNameCollisionError`）。後勝ちで黙って上書きすると、
       戦略が別指標の値を掴んだまま完走する（エラーにならずに誤った結果を返す）。
    5. **新規 `IndicatorPort` 実装クラスは作らない**。既存の
       `simulator.adapter.indicator.registry.PandasIndicatorRegistry`（事前計算系列を
       名前で持つ実装）へ dict を渡す。同型の実装を 2 つ持つと必ず片方だけ腐る。
    6. **指標式は再実装しない**。値は compute の出力をそのまま運ぶ。

CLEAN_ARCH §6: pandas（`pandas.Series` の生成）は本ファイルに閉じる。usecase へは
プレーンな `list[float]` しか渡さない。
"""
from __future__ import annotations

from typing import Any, Collection, Sequence

from simulator.sim_ui.usecase.align_series_to_bars import align_series_to_bars
from simulator.sim_ui.usecase.indicator_models import (
    IndicatorSpec,
    IndicatorSupplyError,
    SeriesBundle,
    SeriesNameCollisionError,
)
from simulator.sim_ui.usecase.indicator_ports import CausalSeriesProbePort


class LiveIndicatorSupply:
    """ライブ compute の系列をバックテストの指標レジストリへ供給する。

    ``probe``: `CausalSeriesProbePort`（実体は
    :class:`~simulator.sim_ui.adapter.causal_series_probe.CausalSeriesProbe`）。
    """

    def __init__(self, *, probe: CausalSeriesProbePort) -> None:
        self._probe = probe

    # --- 整列（プレーン値）----------------------------------------------

    @staticmethod
    def align_bundle(
        bundle: SeriesBundle, *, bar_times: "Sequence[int]"
    ) -> "dict[str, list[float]]":
        """既に計算済みの束をバー時刻列へ整列する（供給の第 2 段）。

        検定 CLI は段 0 で測った束をそのまま渡す（測り直すと供給コストを二重に払う）。
        """
        return {
            name: align_series_to_bars(bundle[name], bar_times) for name in bundle
        }

    def series_values(
        self,
        spec: IndicatorSpec,
        *,
        ref: str,
        timeframe: "str | None",
        bar_times: "Sequence[int]",
    ) -> "dict[str, list[float]]":
        """系列名 → バー時刻列へ整列済みの値列（計算 1 回 + 整列）。"""
        times = list(bar_times)
        if not times:
            return {}
        # 案 ii の窓の右端は供給窓の末尾（案 i の最終窓と揃える＝prefix 関係）。
        bundle = self._probe.series_full(
            spec, ref=ref, timeframe=timeframe, until_time=int(times[-1])
        )
        return self.align_bundle(bundle, bar_times=times)

    # --- レジストリ（pandas 隔離点）--------------------------------------

    def build_registry(
        self,
        *,
        specs: "Sequence[IndicatorSpec]",
        ref: str,
        timeframe: "str | None",
        bar_times: "Sequence[int]",
        selectable: "Collection[str]",
    ) -> Any:
        """既存 `PandasIndicatorRegistry` を構築して返す（index は 0..n-1）。

        ``selectable``: 台帳が選択可能とした系列名（規則 3）。1 指標から 1 系列も
        選べないときは明示エラー（黙って空のレジストリを返さない）。
        """
        import pandas as pd  # 遅延: 技術隔離を本メソッドに閉じる

        from simulator.adapter.indicator.registry import PandasIndicatorRegistry

        allowed = set(selectable)
        times = list(bar_times)
        index = list(range(len(times)))
        series: "dict[str, Any]" = {}
        for spec in specs:
            values = self.series_values(
                spec, ref=ref, timeframe=timeframe, bar_times=times
            )
            chosen = {name: v for name, v in values.items() if name in allowed}
            if not chosen:
                raise IndicatorSupplyError(
                    f"選択可能な系列がありません: {spec.key}"
                    f"（供給系列={sorted(values)}）"
                )
            for name, sequence in chosen.items():
                if name in series:
                    raise SeriesNameCollisionError(f"系列名が衝突しています: {name}")
                series[name] = pd.Series(sequence, index=index, dtype="float64")
        return PandasIndicatorRegistry(series)
