"""規則 S（バー系列の有無と `tick_model` の整合）の唯一の判定点（ISSUE-502 段階 3）。

1. 層名/責務:
    main 層（Composition Root）。Composition Root の DI 構築関数（simulator.main の
    build_interactor）と Settings 写像層（simulator.main.tester_settings の
    kwargs_mapper）の**双方が使う**規則 S を所有する。判定は 1 か所にしかなく、
    両呼出側はここを呼ぶだけである。

2. なぜ親パッケージ本体にも子パッケージにも置かないか（本モジュールの存在理由）:
    規則 S の実体は従来 kwargs_mapper に在り、build_interactor はそれを
    **関数内 import** で呼んでいた。一方 kwargs_mapper / ea_input_map /
    run_from_settings / run_settings_job は `simulator.main` を import する。結果として
    親パッケージと子パッケージが相互に import する双方向の辺（循環）が生まれ、
    関数内 import でしか成立しない構造になっていた（SOLID 精査台帳 2026-09-06 の C-2）。

    辺そのものを消すには、双方が依存する部分を**どちらでもない第三の点**へ移す以外に
    ない（共通の依存先を作れば辺の向きは 2 本とも「内向き」になり、循環は構造的に
    消える）。本モジュールがその第三の点である。逆向き（子パッケージ →
    `simulator.main`）は残るが、それは子から親への一方向であり循環ではない。
    この一方向性は `simulator/tests/unit/test_package_import_acyclicity.py` が
    構文木で機械的に固定する。

    親（`simulator/main/__init__.py`）へ判定を移す案は棄却した：子から親を module 直下
    import する辺が既に 4 本あり、判定を親へ置くと `simulator.main` の巨大な公開面
    全体が Settings 写像層の module 直下依存に入る。判定 1 個のために依存面を広げない。

3. 元 MQL 対応:
    Strategy Tester の Model（Every tick / OHLC / Math calculations）と入力データの
    要否の対応。MT5 は Model により価格系列の要否が変わる。

4. 依存:
    標準: なし
    外部: なし
    プロジェクト内: simulator.adapter.execution.tick_model_registry（`consumes_market_data`
                    ＝要否の宣言を読む唯一の関数）/
                    simulator.domain.tester_settings_exceptions（E-03）

    `simulator.main`（親パッケージ本体）も `simulator.main.tester_settings`
    （子パッケージ）も import しない。ここが「第三の点」であるための不変条件である。
"""
from __future__ import annotations

from simulator.adapter.execution.tick_model_registry import consumes_market_data
from simulator.domain.tester_settings_exceptions import SettingsActivationError

#: 実行要求時の規則 ID（基本設計 §4.5.5）。Settings 写像層は本定数を書き写さず読む。
RULE_DATA_CONSISTENCY: str = "S"


def verify_engine_data_consistency(*, tick_model: str, has_data: bool) -> None:
    """規則 S の**唯一の判定点**（エンジン語彙＝`tick_model` id だけで判定する）。

    要否の宣言は `TickModelSpec.requires_market_data` の 1 箇所にしかなく、本関数は
    `consumes_market_data` 経由でそれを読むだけである（`if math` を持たない＝OCP。
    新しい modelling が増えても本関数は改変不要）。取り違え——バー系列を消費しない
    modelling にデータを与える／消費する modelling に与えない——を E-03 で Fail-Stop する。

    Settings 語彙を持たない呼出側（Composition Root の DI 構築関数。config_overrides を
    素通しで受ける投入経路＝`POST /sim/jobs` の実体）が同じ判定へ到達できるように、
    入力を実効設定 DTO ではなく `tick_model` id にしている。判定をそちらへ写して
    二重化しないための形である（🟡-1）。

    `SettingsActivationError` は決定論 config の例外の派生であり、終了コード翻訳
    （simulator.adapter.exit_codes）でそのまま 2 になる。
    """
    if consumes_market_data(tick_model) != has_data:
        raise SettingsActivationError(
            field="tick_model",
            rule_id=RULE_DATA_CONSISTENCY,
            tick_model=tick_model,
            has_data=has_data,
        )
