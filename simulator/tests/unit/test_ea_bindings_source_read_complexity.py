"""EA 構築の読取の計算量検定（CX-5・プロジェクト絶対命令 2026-08-28）。

なぜ計算量で固定するか:
    形式を見て読み方を選ぶ形は「ヘッダを見る → 読む → もう一度ヘッダを見る」と
    発行が積み上がりやすい。しかも**出力は 1 ビットも変わらない**（同じ DataFrame・
    同じ Bar 列が出る）ため、状態検証では原理的に落ちない。測るのは時間ではなく回数。

継ぎ目:
    `sources.source_for`（データ実体の解決口。registry 用 DataFrame と Bar の読み手を
    1 回の形式判定で返す）。各 EA モジュールの名前空間に束縛されるため、Spy は
    **その EA モジュールの属性**へ被せる
    （`simulator/tests/integration/test_ea_bindings_are_declaration_driven.py` が
    Composition Root の畳み込みを測るときと同じ流儀）。
    ヘッダ読取は `sources.detect_ohlc_form` を数える。

**「使用」の定義（工程 5 🟡-4 の是正・data_path 単位）**:
    1 回の構築が要る形式の事実は「その実体が何形式か」**1 つ**である。したがって
    使用 = Σ(構築ごとの、形式判定が渡された**相異なる data_path の数**)。同じ実体への
    2 回目の判定は出力に何も足さない＝無駄である。

    是正前はここを「使用 = DataFrame の発行数 + 読み手の発行数」と定義していた。これは
    入口の呼び回数をそのまま使用とみなす定義であり、**入口が 2 つある限り恒等的に差 0**
    になる。実測では 1 構築あたりヘッダ読取 2 回（是正前の 3 本は 0 回）へ増えていたのに、
    その増加を原理的に検出できなかった。さらに悪いことに、重複を除いた正しい実装に対して
    は差が負になって**赤くなる**——浪費を仕様へ昇格させる形だった（ISSUE-450 と同型）。

表明:
    1. 発行 − 使用 = 0。同じ実体の形式を 2 回導出しない。
    2. 1 回の構築が解く実体は 1 つ（解決口の発行が構築数を超えない）。
    3. 規模 2 点（EA 2 本 / 3 本）で、発行の増分が構築 1 回ぶんに収まる。
    **回数そのものは焼き込まない**（「N 回呼ばれること」を固定すると浪費が仕様へ昇格する）。

Test Spy は `marketdata/tests/spread_series_fixture.py` の実装を import して使う
（同じ Spy を手書き複製しない）。
"""
from __future__ import annotations

import pytest

from marketdata.symbol_spec_snapshot import OANDA_JAPAN_MT5_LIVE, load_spec_fields
from marketdata.tests.spread_series_fixture import spy
from simulator.main import build_interactor
from simulator.main.ea_bindings import ma_slope, ma_slope_pending, sources, stop_entry_probe

#: 継ぎ目を持つ EA モジュール（本段で形式非依存にした 3 本）。
_EA_MODULES = {
    "MA_Slope_EA": ma_slope,
    "MA_Slope_Pending_EA": ma_slope_pending,
    "StopEntryProbe_EA": stop_entry_probe,
}

#: 規模の 2 点（EA 2 本 / 3 本）。同じ実体（1 つの合成 CSV）を読む。
_TWO_EAS = ("MA_Slope_EA", "MA_Slope_Pending_EA")
_THREE_EAS = ("MA_Slope_EA", "MA_Slope_Pending_EA", "StopEntryProbe_EA")


def _marketdata_csv(tmp_path):
    """気配幅つき marketdata 形式の合成 5 行（3 本ともこの 1 ファイルを読む）。"""
    header = "date,open,high,low,close,volume,up,dn,spread"
    rows = [
        f"2024-01-08 00:{i:02d}:00,{100.0 + i},{101.0 + i},{99.0 + i},{100.5 + i},1.0,0.0,0.0,{70 + i}"
        for i in range(5)
    ]
    path = tmp_path / "md_spread.csv"
    path.write_text("\n".join([header, *rows]) + "\n", encoding="utf-8")
    return str(path)


def _kwargs(csv_path, ea_name):
    """`build_interactor` の必須 18 キー（銘柄仕様は供給元スナップショットが権威）。"""
    return dict(
        data_path=csv_path,
        symbol="JP225",
        period="M1",
        ea_name=ea_name,
        initial_deposit=10_000.0,
        **load_spec_fields(OANDA_JAPAN_MT5_LIVE, "JP225"),
        ma_period=2,
        ma_method="ema",
        lot_size=0.1,
        stop_loss_points=0,
        take_profit_points=0,
    )


def _spy_the_seams(monkeypatch):
    """3 本の EA モジュールの解決口と、ヘッダ読取へ Spy を被せる。"""
    resolutions = [spy(monkeypatch, module, "source_for") for module in _EA_MODULES.values()]
    headers = spy(monkeypatch, sources, "detect_ohlc_form")
    return resolutions, headers


def _measure(monkeypatch, csv_path, ea_names):
    """`ea_names` を 1 本ずつ構築し、発行数と使用数（data_path 単位）を返す。

    ``headers``      : 形式判定の発行数（全構築の合計）。
    ``header_paths`` : **使用**＝Σ(構築ごとの、判定が渡された相異なる data_path 数)。
    構築ごとに区切って数えるため、別の構築が同じ実体を解くことは無駄とみなさない
    （実体の内容は構築の間に変わりうる。無駄なのは *1 回の構築の中での* 再導出である）。
    """
    resolutions, headers = _spy_the_seams(monkeypatch)
    issued, used, built, mark = 0, 0, [], 0
    for ea_name in ea_names:
        built.append(build_interactor(**_kwargs(csv_path, ea_name))[1])
        per_build = headers[mark:]
        mark = len(headers)
        issued += len(per_build)
        used += len({call[0] for call in per_build})
    return {
        "resolutions": sum(len(calls) for calls in resolutions),
        "headers": issued,
        "header_paths": used,
        "builds": len(built),
        "bars": sum(len(list(request.bars)) for request in built),
    }


class TestTheBuildIssuesNoReadItDoesNotUse:
    """発行 − 使用 = 0（使用は data_path 単位＝1 実体の形式は 1 つの事実）。"""

    @pytest.mark.parametrize("ea_names", [_TWO_EAS, _THREE_EAS], ids=["two-eas", "three-eas"])
    def test_a_form_is_decided_once_per_data_entity(self, ea_names, monkeypatch, tmp_path):
        """同じ実体に対する形式判定の重複が 0 であること。"""
        # Arrange / Act
        measured = _measure(monkeypatch, _marketdata_csv(tmp_path), ea_names)
        # Assert: 正の対照（測定が空振りしていない＝実際にバーが読めている）。
        assert measured["bars"] > 0
        assert measured["header_paths"] > 0
        # Assert: 発行 − 使用 = 0。
        assert measured["headers"] - measured["header_paths"] == 0, measured

    @pytest.mark.parametrize("ea_names", [_TWO_EAS, _THREE_EAS], ids=["two-eas", "three-eas"])
    def test_one_resolution_per_build(self, ea_names, monkeypatch, tmp_path):
        """1 回の構築が解く実体は 1 つ（解決口の発行が構築数を超えない）。"""
        # Arrange / Act
        measured = _measure(monkeypatch, _marketdata_csv(tmp_path), ea_names)
        # Assert
        assert measured["builds"] == len(ea_names)
        assert measured["resolutions"] - measured["builds"] == 0, measured


class TestTheReadsDoNotGrowWithTheNumberOfEas:
    """規模 2 点（EA 2 本 / 3 本）でのオーダーの表明（回数は焼き込まない）。"""

    def test_the_issue_count_grows_only_by_the_added_build(self, monkeypatch, tmp_path):
        # Arrange: 同じ実体を 2 本／3 本で読む。
        csv_path = _marketdata_csv(tmp_path)
        # Act
        two = _measure(monkeypatch, csv_path, _TWO_EAS)
        three = _measure(monkeypatch, csv_path, _THREE_EAS)
        # Assert: 正の対照（構築本数は実際に増えている）。
        assert three["builds"] - two["builds"] == 1, (two, three)
        # Assert: 発行の増分は構築 1 回ぶんだけ（EA 本数に対して超線形にならない）。
        assert three["resolutions"] - two["resolutions"] == 1, (two, three)
        assert three["headers"] - two["headers"] == (
            three["header_paths"] - two["header_paths"]
        ), (two, three)
