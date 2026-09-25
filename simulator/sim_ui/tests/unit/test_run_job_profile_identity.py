"""投入から実行プロファイルを特定する規則の検定（ISSUE-511 段階 8-D-3・設計 D-3）。

何を固定するか:
    投入された **実体（``data_path``）** が profile を特定する。実体が一致する profile が
    あればその profile が答えであり、**並びには依存しない**。

是正前に何が偽だったか（実測 2026-09-25・本作業ツリー）:
    ``run_job._build_engine_binding`` は ``next((p for p in datasets() if p.symbol == symbol))``
    で **銘柄一致の先頭**を採っていた。銘柄あたりの profile が 1 本のうちは答えが一意なので
    誤りが表に出ないが、同じ銘柄の系列が 2 本になった時点で「並びで決まる」ようになる。
    決済通貨は非対象判定 N-11（口座通貨 ≠ 銘柄の決済通貨を拒否）の判定データ源であり、
    取り違えても**出力は形式上正しいまま**なので状態検証では検出できない。

一致が無い投入をどう扱うか（依頼者裁定 2026-09-25）:
    投入の実体はカタログの実体でなくてもよい（合成 CSV で経路を確かめる既存の検定群が
    実際にそうしている）。その場合は**銘柄一致の profile 群が決済通貨で合意しているときだけ**
    その値を使い、合意しなければ**名前付きで止める**。こうすると答えは
    「一致 → 合意 → 停止」のいずれかになり、どの段でも並びに依存しない。

値をテスト側に書き写さない:
    銘柄仕様 8 項目は供給元スナップショットから引く（run-options 応答の単体検定と
    同じ流儀）。決済通貨は**実在の通貨コードではない番兵**を使う——番兵が返ってくること自体が
    「投入した profile の値が来た」ことの証拠になる（実在コードだと偶然の一致と区別できない）。
"""
from __future__ import annotations

from dataclasses import fields
from types import SimpleNamespace

import pytest

from marketdata.symbol_spec_snapshot import OANDA_JAPAN_MT5_LIVE, load_spec_fields
from simulator.sim_ui.main import composition_root_jobs, run_job
from simulator.sim_ui.usecase.run_options_ports import RunOptionsPort, RunProfile
from simulator.usecase.models import SymbolSpec
from simulator.usecase.tester_settings import TickModel

#: 同一性の指定（値ではない）。供給元スナップショットの銘柄名。
_SYMBOL = "JP225"

#: 実在の通貨コードではない番兵。どちらの profile の値が来たかを一意に言える。
_CURRENCY_A = "__currency_of_the_first__"
_CURRENCY_B = "__currency_of_the_second__"

#: 投入された実体（カタログのどの実体とも一致しない合成の綴り）。
_FOREIGN_ENTITY = "/nowhere/synthetic_submission.csv"


def _profile(dataset: str, data_path: str, currency: str) -> RunProfile:
    """合成の実行プロファイル（銘柄仕様は供給元から引き、リテラルを持たない）。"""
    return RunProfile(
        dataset=dataset,
        data_path=data_path,
        symbol=_SYMBOL,
        period="M1",
        **load_spec_fields(OANDA_JAPAN_MT5_LIVE, _SYMBOL),
        settlement_currency=currency,
    )


def _pair(*, disagree: bool) -> "list[RunProfile]":
    """同じ銘柄の 2 本（決済通貨が食い違う版 / 合意する版）。"""
    second = _CURRENCY_B if disagree else _CURRENCY_A
    return [
        _profile("first_series", "/entities/first.csv", _CURRENCY_A),
        _profile("second_series", "/entities/second.csv", second),
    ]


def _resolve(profiles, *, data_path, symbol: str = _SYMBOL) -> str:
    """被検査の規則を 1 回呼ぶ。"""
    return run_job._settlement_currency_for_submission(
        profiles, symbol=symbol, data_path=data_path
    )


# --- 1. 実体が特定する（並びに依存しない）-------------------------------------------


#: 2 本の並べ方（宣言順 / 逆順）。**検定本体で分岐しない**ため、並びを引数の側で与える
#: （静的品質検定 T6: テスト本体の分岐は実行経路が入力で変わるため禁止）。
_ORDERS = {"declared": (0, 1), "reversed": (1, 0)}


@pytest.mark.parametrize("order", sorted(_ORDERS))
def test_the_submitted_entity_identifies_the_profile_whatever_the_order(order):
    """投入された実体と一致する profile の値が来る。**2 本目を先頭に入れても同じ**。

    銘柄一致の先頭を採る形では、宣言順の側で別の profile の値が来て落ちる（逆順の側は
    「先頭 == 狙った profile」なので偶然通る——だから並びを 2 点で測る）。
    """
    # Arrange
    pair = _pair(disagree=True)
    profiles = [pair[index] for index in _ORDERS[order]]
    wanted = pair[1]              # 狙うのは常に 2 本目の系列（並びでは変わらない）

    # Act
    measured = _resolve(profiles, data_path=wanted.data_path)

    # Assert
    assert wanted.settlement_currency == _CURRENCY_B          # 空振り防止（狙った側を見ている）
    assert len({p.settlement_currency for p in profiles}) == 2  # 空振り防止（2 本は食い違う）
    assert measured == _CURRENCY_B


def test_the_entity_wins_over_the_symbol_for_every_series():
    """どちらの実体を投入してもその実体の値が来る（片側だけ当たる形を排除する）。"""
    # Arrange
    profiles = _pair(disagree=True)

    # Act
    measured = [_resolve(profiles, data_path=p.data_path) for p in profiles]

    # Assert
    assert measured == [p.settlement_currency for p in profiles]
    assert measured[0] != measured[1]          # 空振り防止（2 つは別の答えである）


def test_the_submitted_entity_is_compared_as_text():
    """投入が `Path` でも同じ実体として一致する（front は文字列・写像層は Path を渡し得る）。"""
    # Arrange
    from pathlib import Path

    profiles = _pair(disagree=True)

    # Act
    measured = _resolve(profiles, data_path=Path(profiles[1].data_path))

    # Assert
    assert measured == _CURRENCY_B


# --- 2. 一致が無い投入: 合意なら使い、食い違えば名前付きで止まる ---------------------


def test_an_unmatched_entity_uses_the_agreed_currency_of_the_symbol():
    """実体が一致しない投入は、銘柄一致の profile 群が**合意している値**を使う。

    合成 CSV で経路を確かめる既存の検定群がこの経路を通る（実体を台帳の実物へ差し替えると
    12 年 / 6.4 年系列を実走することになる）。
    """
    # Arrange
    profiles = _pair(disagree=False)

    # Act
    measured = _resolve(profiles, data_path=_FOREIGN_ENTITY)

    # Assert
    assert len({p.settlement_currency for p in profiles}) == 1   # 空振り防止（合意している）
    assert measured == _CURRENCY_A


def test_a_submission_without_an_entity_uses_the_agreed_currency():
    """実体を伴わない投入（バー系列を消費しない modelling）でも合意値で答える。"""
    # Act
    measured = _resolve(_pair(disagree=False), data_path=None)

    # Assert
    assert measured == _CURRENCY_A


def test_an_unmatched_entity_stops_by_name_when_the_profiles_disagree():
    """食い違うときは**名前付きの例外**で止まり、案内に食い違いの中身が載る。

    固定するのは**案内に何が載っているか**（食い違った profile の ref と、その値、投入された
    実体）であって文面ではない。綴りは 1 つも書き写さず、合成した profile から導く。
    推定値で通すと N-11 の判定が壊れる（沈黙で「通貨一致」扱いになる）。
    """
    # Arrange
    profiles = _pair(disagree=True)

    # Act
    with pytest.raises(run_job.SettlementCurrencyDisagreement) as caught:
        _resolve(profiles, data_path=_FOREIGN_ENTITY)

    # Assert
    message = str(caught.value)
    assert len({p.settlement_currency for p in profiles}) == 2   # 空振り防止（実際に食い違う）
    for profile in profiles:
        assert profile.dataset in message          # どの profile 群か
        assert profile.settlement_currency in message   # どの値で食い違ったか
    assert _FOREIGN_ENTITY in message              # どの投入がどれとも一致しなかったか


def test_the_named_stop_is_still_a_value_error():
    """型は ``ValueError`` の派生（握る側の契約を変えない・`TickTokenMissing` と同型）。"""
    assert issubclass(run_job.SettlementCurrencyDisagreement, ValueError)


def test_an_unregistered_symbol_stops_with_the_symbol_named():
    """銘柄一致の profile が 1 件も無ければ止まる（推定値を発明しない・従来どおり）。"""
    # Arrange
    profiles = _pair(disagree=False)

    # Act
    with pytest.raises(ValueError) as caught:
        _resolve(profiles, data_path=_FOREIGN_ENTITY, symbol="NOT_A_SYMBOL")

    # Assert
    assert "NOT_A_SYMBOL" in str(caught.value)


# --- 3. 結線: 束縛の決済通貨がこの規則を通って来る -----------------------------------


class _FakePort(RunOptionsPort):
    def __init__(self, profiles):
        self._profiles = profiles

    def datasets(self):
        return list(self._profiles)

    def ea_names(self):
        return ["TC24051901"]


def _backtest_of(profile: RunProfile) -> dict:
    """投入 body のうち束縛の組み立てが読む分だけ（銘柄仕様は profile から機械的に写す）。"""
    body = {field.name: getattr(profile, field.name) for field in fields(SymbolSpec)}
    body.update(
        symbol=profile.symbol,
        period=profile.period,
        data_path=profile.data_path,
        leverage=profile.leverage,
    )
    return body


def test_the_engine_binding_takes_its_currency_through_the_entity_rule(monkeypatch):
    """`_build_engine_binding` の決済通貨が、投入された実体の profile の値になる。

    受け口だけを直しても呼出側が繋がっていなければ無音で旧経路が生き残る（ISSUE-291 の型）。
    ここは並びを入れ替えた 2 本を与え、**先頭ではない側**の実体を投入して結線を見る。
    """
    # Arrange
    profiles = list(reversed(_pair(disagree=True)))   # 先頭は _CURRENCY_B の系列
    monkeypatch.setattr(
        composition_root_jobs, "build_run_options_port", lambda: _FakePort(profiles)
    )
    wanted = profiles[1]

    # Act
    binding = run_job._build_engine_binding(
        {"backtest": _backtest_of(wanted)},
        SimpleNamespace(tick_model=TickModel.MATH_CALCULATIONS),
    )

    # Assert
    assert wanted.settlement_currency == _CURRENCY_A            # 空振り防止（先頭ではない側）
    assert profiles[0].settlement_currency == _CURRENCY_B       # 空振り防止（先頭は別の値）
    assert binding.settlement_currency == _CURRENCY_A
