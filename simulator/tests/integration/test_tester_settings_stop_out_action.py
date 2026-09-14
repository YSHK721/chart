"""`.ini` 経路（TESTER_SETTINGS）の `stop_out_action` 既定を固定する（ISSUE-396 (a)）。

⚠️ 本モジュールは**実装より先に書いたテスト**である（Red）。
`kwargs_mapper._config_overrides` は現時点で `stop_out_action` を載せないため、
`TestIniPathStopOutAction::test_ini_path_states_close_and_halt_explicitly` が失敗する。

固定する仕様:
    1. `.ini` 経路の `config_overrides` は `stop_out_action` を明示する
       （エンジン既定 `"fail_stop"` のままだと証拠金割れが `MarginCallError` になり、
       MT5 と同じ「強制決済して完走」を再現できない）。
    2. `binding.config_overrides`（データセット側の権威）が与えた値が優先される（OCP）。
       `.ini` 経路の既定はあくまで「未指定時に補う値」であり、上書きを妨げない。
    3. 明示する値が config スキーマ（`load_config` の pydantic 検証）を通る。
       語の取り違え（例 `"close_and_hault"`）が実行時まで生き残らないようにする。
    4. 既定値 `"close_and_halt"` が MT5 実測 fixture の要求と一致する（下記オラクル）。

既定を `"close_and_halt"` とする根拠（fixture の実読・本モジュールの
`TestStopOutActionMatchesTheMt5Oracle` が固定する）:
    `fixtures/mt5/ma_slope_jp225_202501/` の実 MT5 出力は、証拠金割れの時点で
    「保有玉を強制決済し、テストを完走して確定レポートを出した」ことを示す。
      - `mt5_report/tester.log` 11663 行:
          `2025.01.13 13:07:00   position stop out triggered at 99.95% [#2326 buy 1 JP225 38325.7]`
      - `expected/report.json` 最終 deal（#2327）: `dir="out"` / `time="2025.01.13 13:07:00"` /
        `profit=-30.0` / `balance=3831.0` / `comment="so 99.95% "`
        ＝ stop out が**決済 deal を生成**している（例外中断ではない）。
      - 同 `results` は `total_trades=1163` / `total_net_profit=-6169.0` を持つ
        ＝ 集計まで**完走**している。
      - `case.yaml` `expected_summary.stop_out: true`。
    エンジンの `"fail_stop"` は `MarginCallError` を送出して部分結果を破棄するため
    （`simulator/usecase/run_backtest.py` の 3 箇所の分岐）、この観測と一致しない。
    観測と一致するのは `"close_and_halt"` だけである。
"""
from __future__ import annotations

import json

from simulator.framework.config_loader import load_config
from simulator.main.tester_settings.kwargs_mapper import STOP_OUT_ACTION
from simulator.tests.fixtures.mt5 import load_case
from simulator.tests.integration.test_tester_settings_to_interactor import _kwargs

#: 本テストが参照する MT5 突合ケース（`stop_out: true` を含む唯一の実測ケース）。
_CASE = "ma_slope_jp225_202501"

#: MT5 が stop out 決済 deal に付ける comment の接頭辞（`tester.log` の
#: `position stop out triggered at 99.95%` に対応する。実測値）。
_STOP_OUT_COMMENT_PREFIX = "so "


class TestIniPathStopOutAction:
    """§8.1: `.ini` 経路は `stop_out_action` を明示する（ISSUE-396 (a)）。"""

    def test_ini_path_states_close_and_halt_explicitly(self):
        assert _kwargs()["config_overrides"]["stop_out_action"] == "close_and_halt"

    def test_the_default_is_declared_once_and_read_from_the_module(self):
        # 既定値を 2 箇所に書かない（写像は定数を読むだけ）。
        assert _kwargs()["config_overrides"]["stop_out_action"] == STOP_OUT_ACTION

    def test_binding_config_overrides_take_priority(self):
        # データセット側が権威として別値を持つ場合はそちらが勝つ（OCP・既存の優先順位規則）。
        kwargs = _kwargs(config_overrides={"stop_out_action": "fail_stop"})
        assert kwargs["config_overrides"]["stop_out_action"] == "fail_stop"

    def test_the_emitted_value_passes_the_config_schema(self):
        # 語の取り違えを実行時まで生かさない（`load_config` の Literal 検証を通す）。
        overrides = _kwargs()["config_overrides"]
        assert load_config(dict(overrides)).stop_out_action == STOP_OUT_ACTION


class TestStopOutActionMatchesTheMt5Oracle:
    """既定値の根拠を実 MT5 fixture に固定する（推測値への差し替えを検出する）。"""

    def test_mt5_closed_the_position_and_completed_the_run(self):
        case = load_case(_CASE)
        # case.yaml の要約（可読用）と report.json（最終オラクル）の双方で stop out が「正」。
        assert case.config["expected_summary"]["stop_out"] is True

        # stop out は**決済 deal**として現れる（例外中断なら deal は生成されない）。
        terminal = case.deals[-1]
        assert terminal["dir"] == "out"
        assert (terminal["comment"] or "").startswith(_STOP_OUT_COMMENT_PREFIX)

        # かつ集計まで完走している（部分結果の破棄ではない）。
        assert case.expected["results"]["total_trades"] == 1163.0
        assert case.expected["results"]["total_net_profit"] == -6169.0

    def test_the_stop_out_is_recorded_in_the_tester_log(self):
        # MT5 の tester.log は UTF-16 LE（BOM 付き・実測）で出力される。
        log = (load_case(_CASE).dir / "mt5_report" / "tester.log").read_text(
            encoding="utf-16"
        )
        assert "position stop out triggered at 99.95%" in log
        # stop out の後もテスターは走り切っている（部分結果の破棄ではない）。
        assert "stop out occurred on 41% of testing interval" in log

    def test_the_default_is_the_action_that_reproduces_that_observation(self):
        # 「強制決済して完走」を意味するエンジン語彙は `close_and_halt` のみ
        # （`fail_stop` は `MarginCallError` で部分結果を破棄する）。
        assert STOP_OUT_ACTION == "close_and_halt"

    def test_the_case_fixture_is_self_describing(self):
        # 参照した report.json が本当に当該 MT5 実走の出力であること（取り違え検出）。
        source = json.dumps(load_case(_CASE).expected["source"], ensure_ascii=False)
        assert "MA_Slope_EA" in source or "ReportTester" in source
