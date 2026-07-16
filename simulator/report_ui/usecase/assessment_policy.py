"""IS/OOS 合否方法論の方針オブジェクト（ISSUE-094 🟡-5）。

BuildReportPayload に直書きされていた degradation（劣化率算出）と verdict（合否判定木）の
方法論・閾値を、閾値注入可能な独立方針オブジェクト AssessmentPolicy へ抽出する。これにより
BuildReportPayload 本体は「表示形状の写像」という単一アクターに収束し、合否方法論の変更は
本モジュールへ局所化される。

既定閾値は現行値（詳細設計 §5.3）で不変。判定木の順序・reason 文言も現行と byte 一致
（既定インスタンスでは report.json 出力を不変に保つ）。閾値を注入した場合、reason 文言中の
閾値表示も注入値へ追従する（f-string へ閾値を埋め込む）。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from simulator.report_ui.usecase.report_models import VerdictModel

# degradation 対象指標（詳細設計 §5.3）。挿入順が report.json のキー順を規定するため不変。
_DEFAULT_DEG_KEYS: tuple[str, ...] = (
    "net", "profit_factor", "win_rate", "expectancy", "payoff",
    "return_pct", "max_dd_pct",
)


@dataclass(frozen=True)
class AssessmentPolicy:
    """IS/OOS の劣化率算出と合否判定（degradation / verdict）を担う方針オブジェクト。

    閾値は注入可能で、既定は詳細設計 §5.3 の現行値。
    - ``oos_pf_floor``    : OOS PF がこの値未満なら fail（既定 1.0）
    - ``pf_warn_ratio``   : PF 劣化比（OOS/IS）がこの値未満なら warn（既定 0.7）
    - ``winrate_delta_floor`` : 勝率差(pt) がこの値未満なら reason 追加（既定 -5.0）
    - ``deg_keys``        : degradation を算出する指標名の順序付き集合
    """

    oos_pf_floor: float = 1.0
    pf_warn_ratio: float = 0.7
    winrate_delta_floor: float = -5.0
    deg_keys: tuple[str, ...] = _DEFAULT_DEG_KEYS

    def degradation(self, sum_is: Any, sum_oos: Any) -> dict:
        """IS/OOS サマリーから指標別の劣化率(ratio)と差分(delta)を算出する（§5.3）。"""
        deg: dict = {}
        for k in self.deg_keys:
            i = getattr(sum_is, k)
            o = getattr(sum_oos, k)
            ratio = None if i == 0 else round(o / i, 3)
            deg[k] = {"is": i, "oos": o, "ratio": ratio, "delta": round(o - i, 2)}
        return deg

    def verdict(self, sum_is: Any, sum_oos: Any, deg: dict) -> VerdictModel:
        """合否判定木（§5.3・順序厳守）。deg は本 policy の degradation 出力。"""
        reasons: list[str] = []
        is_net = sum_is.net
        oos_net = sum_oos.net
        oos_pf = sum_oos.profit_factor

        if is_net > 0 and oos_net <= 0:
            result = "fail"
            reasons.append(
                f"IS黒字(+{is_net:.0f})に対しOOS赤字({oos_net:.0f})＝未知区間で優位性消失"
            )
        elif oos_pf < self.oos_pf_floor:
            result = "fail"
            reasons.append(f"OOS PF={oos_pf:.3f}<{self.oos_pf_floor}＝検証区間で損失超過")
        elif (deg["profit_factor"]["ratio"] is not None
              and deg["profit_factor"]["ratio"] < self.pf_warn_ratio):
            result = "warn"
            reasons.append(
                f"PF劣化 比={deg['profit_factor']['ratio']}（OOS/IS<{self.pf_warn_ratio}）"
            )
        else:
            result = "pass"
            reasons.append("OOSでも優位性を維持")

        if deg["win_rate"]["delta"] < self.winrate_delta_floor:
            reasons.append(f"勝率差={deg['win_rate']['delta']}pt 悪化")
        if deg["expectancy"]["ratio"] is not None and deg["expectancy"]["ratio"] < 0:
            reasons.append("期待値が正→負へ反転")

        return VerdictModel(result=result, reasons=reasons)
