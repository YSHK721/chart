"""TESTER_SETTINGS 単体テスト群が共有する合成 `.ini` 生成器（テストヘルパ・非テストモジュール）。

1. 責務:
    corpus（`sample/MQL5/Profiles/Tester/*.ini`・Git 追跡外）に依存せず、
    基本設計 §4.4「標準キー順」・§2.2.3「キー出現順（実測）」と同一構造の
    `.ini` を合成する。キー順・キー集合は**実装の宣言を import** し、本モジュール
    では再宣言しない（下記「再宣言しない理由」）。

2. 含む構造:
    synthetic_tester_map  : `[Tester]` の (キー→値) を標準キー順で組む唯一の関数
    synthetic_ini_lines   : 上記＋`[TesterInputs]` から行原文列を組む唯一の関数
    encode_ini / write_ini: UTF-16LE + BOM + CRLF でのバイト列化・ファイル書出し
    SYNTHETIC_CASES       : 往復検証（T-01b）の 12 ケース

3. 元 MQL 対応:
    なし（テスト用の合成データ生成器）。値は corpus 実測値（基本設計 §2.2.3）
    をそのまま既定値に採る。

4. 依存:
    標準: dataclasses / pathlib / typing
    プロジェクト内: simulator.adapter.tester_settings.ini_codec（標準キー順・許容キー集合）
                    simulator.framework.tester_settings.validation（Expert 専用キー）

**キー順・キー集合を再宣言しない理由**: 本モジュールは合成データの**生成器**であり、
生成物であって期待値ではない。順序表を手書きで複製すると実装との二重管理になり、
必ず片方が腐る（プロジェクト規約「同じコードを手書き複製するな」）。
なお「実装の順序表が corpus 実測順と一致する」ことの**独立検証**は本モジュールでは
行わない。`test_tester_ini_codec.py::TestStandardKeyOrderSourceOfTruth` が
設計文書リテラルおよび corpus 直読との突合として 1 箇所だけで担う。
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from simulator.adapter.tester_settings.ini_codec import (
    SECTION_TESTER,
    SECTION_TESTER_INPUTS,
    STANDARD_KEY_ORDER,
    TESTER_KEYS,
)
from simulator.framework.tester_settings.validation import EXPERT_ONLY_KEYS

#: `.ini` のセクション名（内部設計 §4.1・基本設計 R4）。実装の宣言を参照する。
TESTER_SECTION = SECTION_TESTER
TESTER_INPUTS_SECTION = SECTION_TESTER_INPUTS

#: 書出し既定（R1・R2）。UTF-16LE + BOM + CRLF。
UTF16LE = "utf-16-le"
UTF16BE = "utf-16-be"
CRLF = "\r\n"
LF = "\n"
BOM_CHAR = "﻿"

#: `[Tester]` の標準キー順（実装 `ini_codec.STANDARD_KEY_ORDER` が単一ソース）。
#: 員数は corpus 実測で 18（相異なるキーの和集合）。1 ファイルあたりの実測最大は 15 キー。
#: 基本設計 §2.2.3 本文の「17 キー」は和集合・ファイル内最大のいずれとも一致しない誤記
#: （ISSUE-389。同節の表は 18 行ある）。員数リテラルに依存せず順序表から導出する。
TESTER_KEY_ORDER: tuple[str, ...] = STANDARD_KEY_ORDER

#: 規則 P の許容キー集合（実装 `ini_codec.TESTER_KEYS` が単一ソース）。
SUPPORTED_TESTER_KEYS: frozenset[str] = TESTER_KEYS

# EXPERT_ONLY_KEYS は `framework.tester_settings.validation` からの再エクスポート
# （基本設計 F-12。規則 G / H の対象 8 キー）。上の import 参照。


class _Omit:
    """「そのキーを出力しない」ことを表す番兵（`None` を値として使い分けるため）。"""

    def __repr__(self) -> str:  # pragma: no cover - デバッグ表示のみ
        return "<OMIT>"


#: `synthetic_tester_map` の override で「キーごと消す」ことを表す番兵。
OMIT = _Omit()

#: Expert テストの既定値（corpus 実測値。`TC24051901.JP225.Daily.all_history.100.ini` 相当）。
_EXPERT_DEFAULTS: dict[str, Any] = {
    "Expert": "TC24051903.ex5",
    "Symbol": "JP225",
    "Period": "Daily",
    "Optimization": "0",
    "Model": "1",
    "Dates": "0",
    "ForwardMode": "0",
    "Deposit": "139500",
    "Currency": "JPY",
    "ProfitInPips": "1",
    "Leverage": "10",
    "ExecutionMode": "50",
    "OptimizationCriterion": "0",
    "Visual": "1",
}

#: Indicator テストの既定値（corpus 実測値。`PRO!fit_Band.JP225.H8.all_history.4.ini` 相当）。
_INDICATOR_DEFAULTS: dict[str, Any] = {
    "Indicator": "PRO!fit_Band.ex5",
    "Symbol": "JP225",
    "Period": "H8",
    "Model": "4",
    "Dates": "0",
    "Visual": "1",
}

#: `[TesterInputs]` の実測行（F-13 の 5 分割形式・F-14 の空値形式）。
RANGE5_INPUT_LINES: tuple[str, ...] = (
    "LotSize=0.01||0.01||0.001000||0.100000||N",
    "MAPeriod=3||2||1||22||Y",
    "MAMethod=1||0||0||3||N",
)
SCALAR_INPUT_LINES: tuple[str, ...] = (
    "inpSymbol=",
    "inpTimeFrame=0||0||0||49153||N",
)


def synthetic_tester_map(kind: str = "expert", **overrides: Any) -> dict[str, str]:
    """`[Tester]` の (キー→値) を**標準キー順**で組む唯一の関数。

    Args:
        kind: ``"expert"`` または ``"indicator"``。既定値の土台を選ぶ。
        **overrides: キーごとの上書き。値に :data:`OMIT` を渡すとそのキーを削除する。
            未知キー（規則 P 違反の再現用）は標準キー順の末尾へ、指定順で置く。

    Returns:
        挿入順が標準キー順である ``dict``（Python の dict は挿入順を保つ）。
    """
    if kind == "expert":
        base = dict(_EXPERT_DEFAULTS)
    elif kind == "indicator":
        base = dict(_INDICATOR_DEFAULTS)
    elif kind == "empty":
        base = {}
    else:  # pragma: no cover - 呼出側のタイポを沈黙させない
        raise ValueError(f"未知の kind: {kind!r}")

    merged: dict[str, Any] = dict(base)
    extra_order: list[str] = []
    for key, value in overrides.items():
        if key not in merged and key not in TESTER_KEY_ORDER:
            extra_order.append(key)
        merged[key] = value

    ordered: dict[str, str] = {}
    for key in TESTER_KEY_ORDER:
        value = merged.get(key, OMIT)
        if value is not OMIT:
            ordered[key] = str(value)
    for key in extra_order:
        value = merged[key]
        if value is not OMIT:
            ordered[key] = str(value)
    return ordered


def synthetic_ini_lines(
    kind: str = "expert",
    *,
    inputs: Sequence[str] = (),
    header_comment: str | None = ";synthetic tester settings",
    blank_lines: bool = False,
    **overrides: Any,
) -> tuple[str, ...]:
    """合成 `.ini` の**行原文列**（改行文字を含まない）を組む唯一の関数。

    Args:
        kind: :func:`synthetic_tester_map` に渡す土台。
        inputs: ``[TesterInputs]`` の行原文（``"名前=値"`` 形式）。空なら空セクション。
        header_comment: 1 行目のコメント行（``None`` なら出力しない）。
        blank_lines: ``True`` のとき ``[Tester]`` の直前に空行を 1 行入れる（行種別 BLANK の網羅用）。
        **overrides: :func:`synthetic_tester_map` へ素通しする上書き。
    """
    lines: list[str] = []
    if header_comment is not None:
        lines.append(header_comment)
    if blank_lines:
        lines.append("")
    lines.append(TESTER_SECTION)
    lines.extend(f"{key}={value}" for key, value in synthetic_tester_map(kind, **overrides).items())
    lines.append(TESTER_INPUTS_SECTION)
    lines.extend(inputs)
    return tuple(lines)


def encode_ini(
    lines: Iterable[str],
    *,
    newline: str = CRLF,
    trailing_newline: bool = True,
    encoding: str = UTF16LE,
    bom: bool = True,
) -> bytes:
    """行原文列を `.ini` のバイト列へ変換する（R1・R2・R9 の書出し規則と同一手順）。"""
    body = newline.join(lines)
    if trailing_newline and body:
        body += newline
    if bom:
        body = BOM_CHAR + body
    return body.encode(encoding)


def write_ini(path: Path, lines: Iterable[str], **kwargs: Any) -> Path:
    """`encode_ini` の結果を ``path`` へ書き出し、その ``path`` を返す。"""
    path.write_bytes(encode_ini(lines, **kwargs))
    return path


@dataclass(frozen=True)
class SyntheticCase:
    """往復検証（T-01b）の 1 ケース。"""

    case_id: str
    lines: tuple[str, ...]


def _case(case_id: str, **kwargs: Any) -> SyntheticCase:
    return SyntheticCase(case_id=case_id, lines=synthetic_ini_lines(**kwargs))


#: T-01b の合成 12 件。
#: Expert × {Dates, Custom} × {Forward 0, 3, 4} = 6 件、Indicator × {Dates, Custom} = 2 件、
#: Visual 有無 2 件、空 `[TesterInputs]` 1 件、5 分割入力 1 件。
SYNTHETIC_CASES: tuple[SyntheticCase, ...] = (
    _case("expert_dates_forward0", inputs=RANGE5_INPUT_LINES),
    _case("expert_dates_forward3", ForwardMode="3", inputs=RANGE5_INPUT_LINES),
    _case(
        "expert_dates_forward4",
        ForwardMode="4",
        ForwardDate="2023.05.22",
        inputs=RANGE5_INPUT_LINES,
    ),
    _case(
        "expert_custom_forward0",
        Dates=OMIT,
        FromDate="2020.03.30",
        ToDate="2024.05.18",
        inputs=RANGE5_INPUT_LINES,
    ),
    _case(
        "expert_custom_forward3",
        Dates=OMIT,
        FromDate="2020.03.30",
        ToDate="2024.05.18",
        ForwardMode="3",
        inputs=RANGE5_INPUT_LINES,
    ),
    _case(
        "expert_custom_forward4",
        Dates=OMIT,
        FromDate="2012.01.01",
        ToDate="2012.12.31",
        ForwardMode="4",
        ForwardDate="1970.01.01",
        inputs=RANGE5_INPUT_LINES,
    ),
    _case("indicator_dates", kind="indicator", inputs=SCALAR_INPUT_LINES),
    _case(
        "indicator_custom",
        kind="indicator",
        Dates=OMIT,
        FromDate="2012.01.01",
        ToDate="2024.05.19",
        inputs=SCALAR_INPUT_LINES,
    ),
    # Visual 欠落（F-11: Optimization != 0 のとき Visual キーが存在しない）。
    _case(
        "expert_optimization_without_visual",
        Optimization="1",
        Visual=OMIT,
        OptimizationCriterion="1",
        inputs=RANGE5_INPUT_LINES,
    ),
    # Indicator の Visual 欠落（キーを発明しないことの固定）。
    _case("indicator_without_visual", kind="indicator", Visual=OMIT, inputs=SCALAR_INPUT_LINES),
    # 空 `[TesterInputs]`（corpus 実測: TC24051903.JP225.Daily.all_history.200.ini）。
    _case("expert_empty_inputs", Model="2", ProfitInPips="1", inputs=()),
    # 5 分割入力のみ（Y / N 双方と小数・整数表記の混在）。
    _case(
        "expert_range5_inputs_only",
        Model="0",
        inputs=(
            "LotSize=1||0.1||0.010000||1.000000||N",
            "StopLoss=2500||100.0||10.000000||1000.000000||N",
            "MAPeriod=5||14||1||140||Y",
        ),
    ),
)


def case_ids() -> tuple[str, ...]:
    """`SYNTHETIC_CASES` の ID 列（`pytest.mark.parametrize` の ids に使う）。"""
    return tuple(case.case_id for case in SYNTHETIC_CASES)


def expert_mapping(**overrides: Any) -> dict[str, str]:
    """規則検証テスト用の Expert `[Tester]` マッピング（正常系の土台）。"""
    return synthetic_tester_map("expert", **overrides)


def indicator_mapping(**overrides: Any) -> dict[str, str]:
    """規則検証テスト用の Indicator `[Tester]` マッピング（正常系の土台）。"""
    return synthetic_tester_map("indicator", **overrides)


def reordered(mapping: Mapping[str, str]) -> dict[str, str]:
    """キーの挿入順を逆転した同値マッピング（翻訳優先順位の順序非依存性の検証用）。"""
    return {key: mapping[key] for key in reversed(list(mapping))}
