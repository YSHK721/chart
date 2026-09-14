"""MT5 ストラテジーテスター Settings 層の例外階層（内部設計 §4.5・D-04）。

1. 層名/責務:
    domain 層。Settings（実行条件）の失敗を型で表す。既存 ``domain/exceptions.py``
    は 1 行も変更せず、その ``ConfigError`` を基底として派生系統を**追加**する
    （OCP。既存 CLI の ``except ConfigError`` → 終了コード 2 の翻訳へそのまま載る）。

2. 含む構造:
    SettingsError            : 本系統の基底。``error_id`` / メッセージ雛形 /
                               ``context`` 語彙の検査を一箇所で担う。
    IniFormatError（E-01）〜 SettingsKeyMissingError（E-08）: 失敗種別 8 種。
    各クラスは自分の ``ERROR_ID`` と雛形と必須 context キーを**自分で持つ**
    （外部の対応表を作らない＝取り残しが起きない）。

3. 元 MQL 対応:
    なし（MT5 は設定不正をダイアログで示すのみで例外型を持たない）。本系統は
    ``BACKTEST_DESIGN.md §4.4`` の Fail-Stop 方針を Python の型で表現したもの。

4. 依存:
    標準: typing
    プロジェクト内: simulator.domain.exceptions（ConfigError）
    外部: なし（domain 層は外部ゼロ依存）
"""
from __future__ import annotations

from typing import Any, ClassVar

from simulator.domain.exceptions import ConfigError

# ``context`` に載せてよいキーの語彙（内部設計 §4.5.2）。ここに無いキーを使うと
# 構築時に KeyError となる（呼出側のタイポを実行時に沈黙させない）。
#
# 語彙は**失敗種別ごとに、その種別自身が持つ**（下の各クラスの ``EXTRA_CONTEXT``）。
# 共通の診断値だけをここに置く。外側の層（変換層）でしか生じない診断値
# （``unsupported_id`` / ``requested_window`` 等）を全種別共通の表に集めると、
# 外側の診断項目を 1 つ増やすたびに domain 層のこのファイルを改変することになり、
# 変更が局所化しない。所有者を分けることでその強制を断つ。
BASE_CONTEXT: frozenset[str] = frozenset(
    {
        "path",
        "lineno",
        "line",
        "section",
        "key",
        "keys",
        "value",
        "expected",
        "allowed",
        "rule_id",
        "error_id",
        "reason",
        "subject_kind",
        "fields",
        "validation_errors",
    }
)

# ``line`` の切り詰め長（内部設計 §4.5.2 規約 3）。
_LINE_MAX_CHARS: int = 200


class SettingsError(ConfigError):
    """Settings 層の失敗の基底（``ConfigError`` 派生＝終了コード 2 経路）。

    構築の 2 形式（どちらも同じ結果になる）:

    - 親互換形式: ``SettingsValueError("メッセージ", context={...})``
      （``ConfigError`` と同一シグネチャ。既存呼出との置換可能性を保つ）
    - 雛形形式:   ``SettingsValueError(key="Deposit", value="0", expected="> 0")``
      （メッセージは各クラスの ``MESSAGE`` から生成し、必須 context キーを検査する）

    いずれの形式でも ``context`` は語彙検査・正規化（集合はソート済み list・
    ``line`` は 200 文字で切り詰め・``error_id`` の自動付与）を通る。
    """

    #: 内部設計 §4.5.2 の失敗 ID。サブクラスが自分で宣言する。
    ERROR_ID: ClassVar[str] = ""
    #: メッセージ雛形。``{key}`` 等は正規化済み context ＋派生値で解決する。
    MESSAGE: ClassVar[str] = "設定に失敗しました"
    #: 雛形形式で必須の context キー。
    REQUIRED_CONTEXT: ClassVar[frozenset[str]] = frozenset()
    #: 本種別に固有の context キー（共通語彙 ``BASE_CONTEXT`` への追加分）。
    EXTRA_CONTEXT: ClassVar[frozenset[str]] = frozenset()

    @classmethod
    def allowed_context(cls) -> frozenset[str]:
        """本種別で使ってよい context キーの集合。"""
        return BASE_CONTEXT | cls.EXTRA_CONTEXT

    def __init__(
        self,
        message: str | None = None,
        *,
        context: dict | None = None,
        symbol: str | None = None,
        bar_index: int | None = None,
        timestamp: Any = None,
        **fields: Any,
    ) -> None:
        merged = dict(context or {})
        merged.update(fields)
        normalized = self._normalize_context(merged)
        if message is None:
            self._require(normalized)
            message = self.MESSAGE.format(**self._format_values(normalized))
        super().__init__(
            message,
            context=normalized,
            symbol=symbol,
            bar_index=bar_index,
            timestamp=timestamp,
        )

    # -- context の正規化・検査 --------------------------------------------
    @classmethod
    def _normalize_context(cls, context: dict) -> dict:
        unknown = sorted(set(context) - cls.allowed_context())
        if unknown:
            raise KeyError(
                f"context に語彙外のキーが指定されました: {', '.join(unknown)}"
            )
        normalized = {key: cls._normalize_value(key, value) for key, value in context.items()}
        if cls.ERROR_ID:
            normalized.setdefault("error_id", cls.ERROR_ID)
        return normalized

    @staticmethod
    def _normalize_value(key: str, value: Any) -> Any:
        if isinstance(value, (tuple, set, frozenset)):
            return sorted(value)
        if key == "line" and isinstance(value, str):
            return value[:_LINE_MAX_CHARS]
        return value

    @classmethod
    def _require(cls, context: dict) -> None:
        missing = sorted(cls.REQUIRED_CONTEXT - set(context))
        if missing:
            raise KeyError(
                f"{cls.__name__} に必須の context キーが不足しています: {', '.join(missing)}"
            )

    @staticmethod
    def _format_values(context: dict) -> dict:
        """雛形が参照する値（context ＋ 表示用の派生値）を返す。"""
        values = dict(context)
        keys = context.get("keys")
        values["keys_text"] = ", ".join(str(k) for k in keys) if keys else ""
        values["value_repr"] = repr(context.get("value"))
        return values


class IniFormatError(SettingsError):
    """E-01: `.ini` の字句・構造が規則 R1〜R8 に反する。"""

    ERROR_ID = "E-01"
    MESSAGE = ".ini の書式が不正です: {reason}"
    REQUIRED_CONTEXT = frozenset({"reason", "rule_id"})


class SettingsKeyConflictError(SettingsError):
    """E-02: 同時に指定できないキーが共存する（規則 D・E・F・G）。"""

    ERROR_ID = "E-02"
    MESSAGE = "同時に指定できないキーが存在します: {keys_text}"
    REQUIRED_CONTEXT = frozenset({"keys", "rule_id"})


class SettingsActivationError(SettingsError):
    """E-03: 活性依存に反する実行要求（規則 B・S）。"""

    ERROR_ID = "E-03"
    MESSAGE = "設定の活性依存に反する実行要求です: {field}"
    REQUIRED_CONTEXT = frozenset({"field", "rule_id"})
    EXTRA_CONTEXT = frozenset({"field", "tick_model", "has_data"})


class SettingsValueError(SettingsError):
    """E-04: 値の型・範囲・書式が不正（規則 I〜N・R10・R11）。"""

    ERROR_ID = "E-04"
    MESSAGE = "設定値が不正です: {key}={value_repr}"
    REQUIRED_CONTEXT = frozenset({"key", "value", "rule_id"})


class UnknownSettingValueError(SettingsError):
    """E-05: 列挙に存在しない値（規則 O・R13）。"""

    ERROR_ID = "E-05"
    MESSAGE = "未知の設定値です: {key}={value_repr}"
    REQUIRED_CONTEXT = frozenset({"key", "value", "rule_id"})
    EXTRA_CONTEXT = frozenset({"tbd"})


class UnknownSettingKeyError(SettingsError):
    """E-06: 対象外のキー（規則 P・R12）。"""

    ERROR_ID = "E-06"
    MESSAGE = "未知の設定キーです: {key}"
    REQUIRED_CONTEXT = frozenset({"key", "rule_id"})


class UnsupportedSettingError(SettingsError):
    """E-07: 本実装が保証しない設定の実行要求（§4.6 N-01〜N-16）。

    保証境界の判定は変換層（`main/tester_settings`）が行うため、そこでしか生じない
    診断値（対象 ID・要求窓・実バー範囲・EA 名・TBD 番号）は本種別が所有する。
    """

    ERROR_ID = "E-07"
    MESSAGE = "本実装が対象としない設定です: {unsupported_id} ({field}={value_repr})"
    REQUIRED_CONTEXT = frozenset({"unsupported_id", "field", "value", "reason"})
    EXTRA_CONTEXT = frozenset(
        {
            "unsupported_id",
            "field",
            "tbd",
            "tick_model",
            "ea_name",
            "requested_window",
            "actual_range",
        }
    )


class SettingsKeyMissingError(SettingsError):
    """E-08: 必須キーの欠落（規則 F・H・R）。"""

    ERROR_ID = "E-08"
    MESSAGE = "必須の設定キーが不足しています: {keys_text}"
    REQUIRED_CONTEXT = frozenset({"keys", "rule_id"})
