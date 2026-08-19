"""A-SettingsIniValidator: Tester Settings の受付検証（:class:`SettingsValidationPort` 実装）。

責務（SRP）: **翻訳だけ**。設定規則（B〜Q）は 1 つも持たない。検証の実体は
`framework/tester_settings/loader.tester_settings_from_mapping`（規則全通の単一ソース）で
あり、本クラスはその `SettingsError` を受付拒否の語彙（`JobSubmissionInvalidError`＝
adapter が 400 へ翻訳する例外）へ写すだけである。

なぜ委譲か（複製禁止・設計 §18.3）: 受付側に条件表を書くと検証が 2 実装になり、規則の
改訂に片方だけが追随する。実行段（`run_job` → `tester_settings_from_mapping`）と受付段が
**同じ実体**を通ることで、「受付は通ったのに実行だけ落ちる」という遅い失敗も同時に消える。

なぜ生トークンを受けるか: `.ini` ファイルや型付き JSON DTO を境界に置くと、BOM / CRLF の
混入と検証の第 2 実装を front 側へ作ることになる。境界は (キー→生トークン) と行原文に
限り、符号化と往復（NFR-02）は framework の単一ソースが担う。

文言に診断値を載せる理由: 終了コードや型名だけでは「どの規則のどの値が悪いか」が投入者へ
届かない。`context` の `rule_id` / `key` / `value` / `error_id` を本文へ載せ、無音で
「なんとなく 400」になるのを防ぐ。
"""
from __future__ import annotations

from typing import Any, Mapping, Sequence

from simulator.domain.tester_settings_exceptions import SettingsError
from simulator.framework.tester_settings import tester_settings_from_mapping
from simulator.sim_ui.usecase.job_models import JobSubmissionInvalidError
from simulator.sim_ui.usecase.job_ports import SettingsValidationPort

#: 文言へ載せる診断値（`context` の語彙のうち投入者が直せるもの）。値の**表示順**を
#: 決めるだけの表であり、規則の宣言ではない。
_DIAGNOSTIC_KEYS: "tuple[str, ...]" = ("rule_id", "error_id", "key", "value")


def _diagnostics(error: SettingsError) -> str:
    """`SettingsError.context` から診断値を `k=v` 形式で並べる（無い項目は出さない）。"""
    context: "Mapping[str, Any]" = getattr(error, "context", None) or {}
    parts = [f"{key}={context[key]!r}" for key in _DIAGNOSTIC_KEYS if key in context]
    return "・".join(parts)


class SettingsIniValidator(SettingsValidationPort):
    """`tester_settings_from_mapping` へ委譲する :class:`SettingsValidationPort` 実装。"""

    def validate(self, tester: "Mapping[str, str]", inputs: "Sequence[str]") -> None:
        try:
            tester_settings_from_mapping(dict(tester), list(inputs))
        except SettingsError as error:
            # `SettingsError` **だけ**を翻訳する。想定外の例外まで 400 に化けさせると、
            # 実装の壊れ（AttributeError 等）が「利用者の設定が悪い」に見えてしまう。
            diagnostics = _diagnostics(error)
            suffix = f"（{diagnostics}）" if diagnostics else ""
            raise JobSubmissionInvalidError(
                f"Tester Settings が設定規則に反しています: {error}{suffix}"
            ) from error
