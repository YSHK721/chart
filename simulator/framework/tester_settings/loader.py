"""Settings の `.ini` ロード / 書出し facade（API-01〜API-04）。

1. 層名/責務:
    framework 層。字句層（`adapter.tester_settings.ini_codec`）と検証層
    （`framework.tester_settings.validation`）を束ね、呼出側に 4 つの関数だけを
    見せる。書式（BOM・改行・キー順・値の表記）は字句層、意味（型・値域・活性
    依存）は検証層が持ち、本モジュールは**繋ぐだけ**で規則を再実装しない。
    pydantic はここでは import しない（内部設計 §3.3 I-3）。

2. 含む構造:
    load_tester_settings          : API-01。`.ini` → ``TesterSettings``（``source`` 付き）。
    dump_tester_settings          : API-02。``TesterSettings`` → `.ini`（既存時は失敗）。
    tester_settings_from_mapping  : API-03。(キー→値) → ``TesterSettings``（生トークンを
                                    ``source`` に保持）。
    tester_settings_to_mapping    : API-04。``TesterSettings`` → (キー→値)（``source``
                                    を持つ設定では送出例外なし。射程は各 docstring）。

3. 元 MQL 対応:
    MT5 が Settings タブをシリアライズした `.ini`（2 セクション
    `[Tester]` / `[TesterInputs]`）の読み書きに対応する。

4. 依存:
    標準: logging / pathlib / collections.abc
    外部: なし（pydantic は validation.py だけが持つ）
    プロジェクト内: simulator.adapter.tester_settings.ini_codec /
                    simulator.domain.tester_settings_exceptions /
                    simulator.framework.tester_settings.validation /
                    simulator.usecase.tester_settings

監査ログ（内部設計 §7.3 D-07）:
    ロガーは ``simulator.tester_settings`` 1 本（字句層と同一。子ロガーを作らない）。
    INFO（`bytes` / `sha256` / `key_count`）はバイト列を知る字句層が記録するため
    本モジュールでは再記録しない。ERROR は例外を送出する境界関数で 1 回だけ記録する。
"""
from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from dataclasses import replace
from pathlib import Path

from simulator.adapter.tester_settings.ini_codec import (
    SECTION_TESTER,
    SECTION_TESTER_INPUTS,
    build_document,
    document_from_entries,
    read_document,
    write_document,
)
from simulator.domain.tester_settings_exceptions import SettingsError
from simulator.framework.tester_settings.validation import build_settings
from simulator.usecase.tester_settings.models import IniDocument, IniLineKind, TesterSettings

#: 監査ログの唯一のロガー（字句層と同じ名前＝同一概念に複数の呼び名を作らない）。
LOGGER = logging.getLogger("simulator.tester_settings")


def _split_document(document: IniDocument) -> tuple[dict[str, str], tuple[str, ...]]:
    """``IniDocument`` を `[Tester]` の (キー→値) と `[TesterInputs]` の行原文へ分ける。

    セクションの有無・出現順（R4）と `[Tester]` のキー重複（R5）は字句層の ``parse``
    が既に Fail-Stop で拒否しているため、ここでは再判定しない。
    """
    tester = dict(document.entries(SECTION_TESTER))
    inputs = tuple(
        line.text
        for line in document.lines
        if line.section == SECTION_TESTER_INPUTS and line.kind is IniLineKind.ENTRY
    )
    return tester, inputs


def _log_error(exc: SettingsError, *, path: str | None) -> None:
    """境界関数で 1 回だけ ERROR を記録する（内部関数は記録しない）。"""
    LOGGER.error(
        "Settings の処理に失敗しました: %s",
        exc,
        extra={"path": path, "context": getattr(exc, "context", {})},
    )


def load_tester_settings(path: str | Path) -> TesterSettings:
    """`.ini` を読み込み検証済みの ``TesterSettings`` を返す（API-01）。

    事前条件: ``path`` が存在し、字句層のサイズ上限（1 MiB）以下であること。
    事後条件: ``source`` に ``IniDocument`` を保持し、規則 B〜Q を適用済みであること。
    送出例外: ``SettingsError`` 系（E-01 / E-02 / E-03 / E-04 / E-05 / E-06 / E-08）と
        ``FileNotFoundError``（呼出側のパス誤りであり `.ini` の書式問題ではないため
        翻訳しない＝内部設計 §4.5.3）。
    """
    text_path = str(path)
    try:
        document = read_document(path)
        tester, inputs = _split_document(document)
        return build_settings(
            tester,
            inputs,
            source=document,
            header_comment=document.header_comment(),
            path=text_path,
        )
    except SettingsError as exc:
        _log_error(exc, path=text_path)
        raise


def dump_tester_settings(settings: TesterSettings, path: str | Path) -> None:
    """``TesterSettings`` を `.ini` へ書き出す（API-02）。

    事前条件: ``path`` が未存在であること（既存時は ``FileExistsError``）。
    事後条件: ``source`` があれば読込元とバイト列一致で復元し（R9）、無ければ標準
        キー順で新規生成する（コメント行は生成しない＝R3）。
    送出例外: ``SettingsError`` 系（E-01 / E-04）／``FileExistsError``。
    """
    text_path = str(path)
    try:
        document = settings.source if settings.source is not None else build_document(settings)
        write_document(document, path)
    except SettingsError as exc:
        _log_error(exc, path=text_path)
        raise


def tester_settings_from_mapping(
    tester: Mapping[str, str],
    inputs: Sequence[str] = (),
) -> TesterSettings:
    """(キー→値) から ``TesterSettings`` を構築する（API-03）。

    事前条件: キーは `.ini` と同じ CamelCase、値は生トークン文字列。``inputs`` は
        `[TesterInputs]` の行原文（``"名前=値"``）。
    事後条件: 受け取った生トークンから組み立てた ``IniDocument`` を ``source`` に
        保持する（是正 1 で改定。旧契約は ``source is None``）。符号化は新規生成の
        既定（UTF-16LE + BOM・CRLF）であり、キー順は受け取った順である。
    送出例外: ``SettingsError`` 系（E-01 / E-02 / E-03 / E-04 / E-05 / E-06 / E-08）。

    生トークンを保持する理由（レビュー指摘 🟡-1）: 捨てると、検証層が受理できる表記
    のうち字句層の整形器が出力できないもの（`Deposit=139500.50`）が「読めるが写像
    できない値」になり、API-04 が例外を送出する（内部設計 §6「送出例外: なし」に反
    する）。保持すれば受理集合と出力集合が一致し、API-03 の像に対して API-04 は例外を
    送出しなくなる（``source`` を持たない直接構築物は射程外＝API-04 の docstring 参照）。

    検証を先に通してから文書を組み立てる。順序を逆にすると、字句層の書式違反（E-01）
    が検証層の規則違反（E-02〜E-08）を覆い隠し、例外の決定論（§4.3.3 の優先順位表）
    が経路によって変わるためである。
    """
    try:
        settings = build_settings(tester, inputs, source=None, header_comment=None, path=None)
        return replace(settings, source=document_from_entries(tester, inputs))
    except SettingsError as exc:
        _log_error(exc, path=None)
        raise


def tester_settings_to_mapping(settings: TesterSettings) -> dict[str, str]:
    """``TesterSettings`` を `[Tester]` の (キー→値) へ戻す（API-04）。

    事前条件: なし。
    事後条件: ``source`` があればその `[Tester]` エントリを**生トークンのまま**返す
        （キー順も読込元・入力どおり）。``source`` が無い（プログラムから直接
        ``TesterSettings`` を構築した）場合のみ標準キー順で整形して返す。値が存在
        しないキーは含めない（キーを発明しない）。
    送出例外: ``source`` を持つ設定（API-01 / API-03 の像）では **なし**。``source`` を
        持たない直接構築物では新規生成経路（``build_document``）の Fail-Stop が残る
        （非整数 `Deposit` は E-04・`rule_id="R7"`）。内部設計 §6 API-04 / 本文 L736。
        実測: ``TesterSettings(..., deposit=139500.5, source=None)`` は E-04 を送出する
        （`test_to_mapping_fail_stops_on_a_non_integer_deposit_without_source` が固定）。

    ``source`` を優先する理由（是正 1・レビュー指摘 🟡-1）: 型付き値から整形し直すと、
    字句層の整形器が表記を確定できない値（`Deposit=139500.50`）で例外になり、
    「読めるが写像できない値」が生じる。原文があるならそれが唯一の正解である（R7）。

    ``source`` が無い経路の値の表記（数値・日付・真偽・時間足ラベル）とキー順は字句層
    の ``build_document`` が唯一の実装であり、本関数はその生成物を読み取るだけである
    （表記規則を検証層に書き直さない＝`Deposit=139500` が `139500.0` にならない）。
    """
    document = settings.source if settings.source is not None else build_document(settings)
    return dict(document.entries(SECTION_TESTER))


# API 名（内部設計 §6 で確定）が pytest の既定収集グロブ ``test*`` に一致するため、
# これらを import したテストモジュールで「テスト関数」として収集されてしまう。
# 収集対象外であることを pytest 公式の opt-out 属性で明示する（API 名は改名しない。
# pyproject の ``python_functions`` を書き換える案は既存ファイルの改変になるため採らない）。
tester_settings_from_mapping.__test__ = False
tester_settings_to_mapping.__test__ = False
