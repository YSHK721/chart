"""EA 入力の束縛（内部設計 §4.4.1・D-02）と `ea_stem` の無害化（§7.2・T-15）を固定する。

⚠️ 本モジュールは**実装より先に書いたテスト**である（フェーズ 3 = Red）。
`simulator.main.tester_settings.ea_input_map` は未実装のため、現時点では
**収集エラー（ImportError）** になる（アサート失敗ではない）。

固定する仕様:
    1. `scalar_converter_for` は `build_interactor` の**型注釈**から変換器を導く
       （§4.4.1「写像表を手書きしない」）。注釈の実測: `ma_period: int` /
       `lot_size: float` / `ma_method: str`（`inspect.signature(..., eval_str=True)`）。
    2. 変換規約（§4.4.1 の表）は Fail-Stop であり沈黙変換しない。
       int は `^[+-]?\\d+$` のみ受理（`1.0` / 空文字 / 空白付きを拒否）、
       float は指数表記・`inf` / `nan` を拒否、str は任意を受理。
    3. `EA_INPUT_BINDINGS` の初期内容は**空**（§4.4.1 確定事項。実証できない対応を
       推測で埋めない）。
    4. 未登録の入力名は `ConfigError` であり**沈黙破棄されない**（§6.2）。
    5. `ea_stem` は `pathlib.Path` を経由せず語幹だけを取り出す（K-18・§7.2）。

例外型について: §4.4.1 は「Fail-Stop・沈黙変換の禁止」を定めるが、**変換器単体**の例外
クラスまでは確定していない。したがって変換器の水準では「値を返さず送出すること」
（＝沈黙変換しないこと）だけを固定し、例外クラスの契約は公開境界である
`bind_ea_inputs` の水準で `ConfigError`（§4.5.1 の階層＝既存 CLI の
`except ConfigError` → 終了コード 2 に載る・T-13）として固定する。
"""
from __future__ import annotations

import inspect

import pytest

from simulator.domain.exceptions import ConfigError
from simulator.main import build_interactor
from simulator.main.tester_settings.ea_input_map import (
    EA_INPUT_BINDINGS,
    EaInputBinding,
    bind_ea_inputs,
    ea_stem,
    scalar_converter_for,
)
from simulator.usecase.tester_settings.enums import InputForm
from simulator.usecase.tester_settings.models import TesterInput


def _scalar(name: str, current: str) -> TesterInput:
    """`[TesterInputs]` の SCALAR 行 1 件（F-14 形式）。"""
    return TesterInput(name=name, form=InputForm.SCALAR, current=current, raw=f"{name}={current}")


def _register(monkeypatch, ea_name: str, name_to_param: dict[str, str]) -> None:
    """`EA_INPUT_BINDINGS` へ一時登録する（§4.4.1 の拡張点＝1 エントリずつ追加）。

    変換器は `scalar_converter_for`（`build_interactor` の注釈が単一ソース）から導き、
    型の対応表をテスト側に持たない。`monkeypatch.setitem` により初期空性は壊れない。
    """
    monkeypatch.setitem(
        EA_INPUT_BINDINGS,
        ea_name,
        {
            ini_name: EaInputBinding(param=param, convert=scalar_converter_for(param))
            for ini_name, param in name_to_param.items()
        },
    )


class TestScalarConverterSource:
    """変換器の型は `build_interactor` の注釈が決める（§4.4.1）。"""

    @pytest.mark.parametrize(
        ("param", "annotation"),
        [("ma_period", int), ("lot_size", float), ("ma_method", str), ("stops_level", int)],
    )
    def test_converter_matches_the_declared_annotation(self, param, annotation):
        # Arrange: 注釈は写像先のシグネチャが単一ソース（テスト側に型表を持たない）
        declared = inspect.signature(build_interactor, eval_str=True).parameters[param].annotation
        # Act
        converted = scalar_converter_for(param)("3" if annotation is not str else "sma")
        # Assert
        assert declared is annotation
        assert type(converted) is annotation


class TestIntConverter:
    """`int`: `^[+-]?\\d+$` のみ受理（§4.4.1 の表）。"""

    @pytest.mark.parametrize(("text", "expected"), [("3", 3), ("+7", 7), ("-7", -7), ("0", 0)])
    def test_accepts_plain_integers(self, text, expected):
        assert scalar_converter_for("ma_period")(text) == expected

    @pytest.mark.parametrize("text", ["1.0", "", " 1", "1 ", "1e3", "0x10", "true"])
    def test_rejects_non_integer_text(self, text):
        # 沈黙変換（`int(float("1.0"))` 等）を禁ずる: 桁を落とした値が口座計算へ届く。
        # 変換器単体の例外クラスは §4.4.1 が確定していないため「送出すること」を固定する。
        with pytest.raises(Exception):
            scalar_converter_for("ma_period")(text)


class TestFloatConverter:
    """`float`: `^[+-]?\\d+(\\.\\d+)?$` のみ受理（指数表記・`inf` / `nan` を拒否）。"""

    @pytest.mark.parametrize(("text", "expected"), [("1", 1.0), ("0.01", 0.01), ("-2.5", -2.5)])
    def test_accepts_plain_decimals(self, text, expected):
        assert scalar_converter_for("lot_size")(text) == pytest.approx(expected)

    @pytest.mark.parametrize("text", ["1e3", "1E3", "inf", "-inf", "nan", "", " 1.0", "1.0 ", ".5"])
    def test_rejects_exponent_and_non_finite_text(self, text):
        with pytest.raises(Exception):
            scalar_converter_for("lot_size")(text)


class TestStrConverter:
    """`str`: 任意（`||` 非包含は `TesterInput` 構築時に保証済み＝§4.4.1）。"""

    @pytest.mark.parametrize("text", ["sma", "ema", "", " ", "JP225_ver24051601"])
    def test_accepts_any_text_verbatim(self, text):
        assert scalar_converter_for("ma_method")(text) == text


class TestBoolConverterReachability:
    """`bool` 行（§4.4.1）は現行シグネチャからは到達できない。

    §4.4.1 の表は `bool` の変換規約（`true` / `false` のみ受理し `True` / `1` / `yes` を
    拒否）を定めるが、`build_interactor` の注釈に `bool` は 1 つも存在しない（実測）。
    したがって `scalar_converter_for` 経由で bool 変換器を得る経路は現在存在しない。

    本テストはその事実を固定し、`bool` 引数が追加された時点で落ちるようにする
    （落ちたら §4.4.1 bool 行の受理 / 拒否テストを書く合図）。沈黙して未検証のまま
    bool 入力が通る状態を作らない。
    """

    def test_build_interactor_declares_no_bool_parameter(self):
        annotations = [
            p.annotation for p in inspect.signature(build_interactor, eval_str=True).parameters.values()
        ]
        assert bool not in annotations


class TestBindingTableIsEmpty:
    """§4.4.1 確定事項: 束縛表の初期内容は空（推測で埋めない）。"""

    def test_ea_input_bindings_starts_empty(self):
        assert EA_INPUT_BINDINGS == {}

    def test_binding_holds_param_name_and_converter(self):
        # Arrange / Act: 2 項束縛（引数名＋変換器）であることの構造固定
        binding = EaInputBinding(param="ma_period", convert=scalar_converter_for("ma_period"))
        # Assert
        assert binding.param == "ma_period"
        assert binding.convert("21") == 21


class TestBindEaInputs:
    """未登録の入力名は沈黙破棄せず `ConfigError`（§6.2・§4.4.1）。"""

    def test_unregistered_input_name_raises_instead_of_being_dropped(self):
        inputs = (_scalar("MaxTradesPerDay", "3"),)
        with pytest.raises(ConfigError) as excinfo:
            bind_ea_inputs("TC24051901", inputs)
        # 沈黙破棄されていないこと（例外が入力名を示す）
        assert "MaxTradesPerDay" in str(excinfo.value)

    def test_unregistered_input_is_not_silently_returned_as_empty_mapping(self):
        # 「未登録なら空 dict を返す」実装は入力を沈黙破棄する。これを禁ずる。
        with pytest.raises(ConfigError):
            bind_ea_inputs("TC24051901", (_scalar("CheckMarketHours", "true"),))

    def test_no_inputs_binds_nothing(self):
        # 入力が無いとき値を発明しない（既定値の捏造禁止）
        assert bind_ea_inputs("TC24051901", ()) == {}

    def test_registered_input_is_converted_to_the_annotated_type(self, monkeypatch):
        _register(monkeypatch, "TC24051901", {"MAPeriod": "ma_period"})
        bound = bind_ea_inputs("TC24051901", (_scalar("MAPeriod", "21"),))
        assert bound == {"ma_period": 21}

    @pytest.mark.parametrize("bad", ["1.0", "", " 1", "true"])
    def test_conversion_failure_surfaces_as_config_error(self, monkeypatch, bad):
        # 公開境界での例外契約（§4.5.1・T-13）: 生の ValueError を層外へ漏らさない。
        # 漏らすと CLI の `except ConfigError` を素通りし、終了コード 2 にならない。
        _register(monkeypatch, "TC24051901", {"MAPeriod": "ma_period"})
        with pytest.raises(ConfigError):
            bind_ea_inputs("TC24051901", (_scalar("MAPeriod", bad),))


class TestEaStem:
    """K-18: `Expert` / `Indicator` の値をファイルシステムアクセスに用いない（§7.2）。"""

    def test_windows_traversal_path_is_reduced_to_the_stem(self):
        # T-15: `..\\..\\etc\\passwd.ex5` → `passwd`
        assert ea_stem("..\\..\\etc\\passwd.ex5") == "passwd"

    @pytest.mark.parametrize(
        ("subject_path", "expected"),
        [
            ("TC24051903.ex5", "TC24051903"),
            ("260620-01_limit_stop.ex5", "260620-01_limit_stop"),
            ("PRO!fit_Band.ex5", "PRO!fit_Band"),
            ("Experts\\Examples\\Moving Average.ex5", "Moving Average"),
            ("C:\\Users\\x\\MQL5\\Experts\\my_first_ea.ex5", "my_first_ea"),
        ],
    )
    def test_stem_drops_directory_prefix_and_ex5_suffix(self, subject_path, expected):
        assert ea_stem(subject_path) == expected

    @pytest.mark.parametrize(
        ("subject_path", "expected"),
        [
            # `.ex5` の除去は 1 回だけ（`removesuffix` の意味）
            ("a.ex5.ex5", "a.ex5"),
            # POSIX 区切りは区切りとして扱わない（Windows 表記だけを分解する）
            ("dir/sub/EA.ex5", "dir/sub/EA"),
        ],
    )
    def test_stem_keeps_what_the_postcondition_does_not_promise_to_remove(
        self, subject_path, expected
    ):
        """docstring の事後条件が主張してよい範囲を反例で固定する（🔵 指摘の是正）。

        以前の docstring は「返る文字列にパス区切り（``\\``）と ``.ex5`` は含まれない」
        と書いていたが、この 2 例が反例である（実測）。docstring を実挙動へ合わせた
        うえで、その実挙動をここで固定する。

        安全性への影響はない: 語幹の用途は `known_ea_names` への**集合所属判定のみ**
        であり、パスとして解決しない。所属しなければ N-01 で実行前に止まる。
        """
        assert ea_stem(subject_path) == expected

    def test_a_stem_that_keeps_a_separator_is_still_rejected_by_membership(self):
        # 上の「除去しきらない」挙動が実行対象の選択に影響しないことを、用途の側で測る。
        assert ea_stem("dir/sub/EA.ex5") not in frozenset({"EA", "TC24051901"})
