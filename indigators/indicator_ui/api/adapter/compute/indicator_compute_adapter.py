"""IndicatorComputeAdapter（内部設計書 §3.3.1）— 既存 add_* 隔離点（唯一）。

FakeChart を生成 → CallBinding で実 add_* を呼出 → FakeChart 収集結果を系列 JSON
（§6.3.2 / §6.3.3）へ変換する。例外は §6.3.4 / §7.4 の error.type へ翻訳する。

既存 src は read-only import（改変禁止）。描画ライブラリは import しない。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import partial
from typing import Any, Callable

from adapter.compute.call_binding import (
    CallBinding,
    requires_time,
    value_error_declarations,
    value_error_types,
)
from adapter.compute.fake_chart import FakeChart

# 必須 OHLC 列（全 3 指標共通。ComputeEntry.required_columns と一致・§3.1.3）。
_REQUIRED_COLUMNS = ("open", "high", "low", "close")

# time 必須の判定は call_binding._TABLE の per-指標宣言（``time_required``）を唯一の真実源とし、
# ``requires_time(compute_id)`` で参照する（SOLID 是正 OCP-1: 従来の adapter ハードコード集合
# ``{"tgp_btlm", "profit_band"}`` を廃止。time 必須指標を増やしても本 adapter は改変不要）。

# error.type → HTTP ステータス対応（§6.3.4 / §7.4）の単一定義は中立共有パッケージ
#   api_shared.http_contract へ移設（ISSUE-094 🔵-11: HTTP 契約の所有者は配信殻であり
#   marketdata のどのアクターでもないため）。本名は再エクスポートで維持（controller・server 殻・
#   既存テストの import 互換）。
from api_shared.http_contract import ERROR_STATUS  # noqa: F401


@dataclass
class ComputeError(Exception):
    """§6.3.4 計算 API エラー。``error_type`` は翻訳済みの type。

    ``violations``（ISSUE-283）: 指標が申告した**機械可読**な診断（例: 履歴不足の
    ``requiredBars`` / ``actualBars``）。文言の解析を上位に強いないための構造化フィールドで、
    既定は空。応答 body の ``error.violations`` へそのまま載る。
    """

    error_type: str
    message: str
    violations: "list[dict]" = field(default_factory=list)

    def __str__(self) -> str:  # pragma: no cover - 表示補助
        return f"{self.error_type}: {self.message}"


def _has_columns(df: Any) -> bool:
    cols = {str(c).lower() for c in df.columns}
    return all(c in cols for c in _REQUIRED_COLUMNS)


# =========================================================================== #
# 宣言駆動の ValueError 翻訳境界（ISSUE-098 🟡-5・LSP 是正 LSP-3 → SOLID 是正 OCP-3）
#
# ある指標は「必須バケット空」(empty_series) と「検証失敗」(validation) のように、同じ
# ``ValueError`` を二意味で送出する。従来は日本語メッセージ片を照合していたが、指標 src が専用型
# （ValueError サブクラス・後方互換）を持つならそれで *型* 識別できる。
#
# OCP-3: どの型をどの error.type へ翻訳するかは **_TABLE の ``value_error_types`` 宣言**が唯一の
# 真実源であり、本 adapter には指標名も専用例外型も現れない（``requires_time`` と同型）。
# 二意味 ValueError を持つ指標が増えても本ファイルは改変しない。なお「空入力」由来の ValueError
# （core.py の "空です"）は compute 冒頭の ``len(df) == 0`` pre-check で確定済みのため
# invoke 後には到達しない。


def _translate_declared_value_error(compute_id: str, exc: ValueError) -> ComputeError:
    """宣言された「ValueError 下位型 → error.type」で翻訳する（未一致は validation）。

    型ローダは遅延評価（指標 src は翻訳が要るときだけロードされる）。宣言された型は
    ValueError のサブクラスであるため、既存の ``except ValueError`` 経路はそのまま捕捉する。
    """
    for error_type, type_loader in value_error_types(compute_id).items():
        if isinstance(exc, type_loader()):
            return ComputeError(error_type, str(exc))
    return ComputeError("validation", str(exc))


#: compute_id → ValueError 専用翻訳器。_TABLE の ``value_error_types`` 宣言からの**導出値**で
#: あり、独立した定義（指標名リテラル）を持たない。未登録指標は汎用 validation へ一様翻訳される。
_VALUE_ERROR_TRANSLATORS: dict[str, Callable[[ValueError], ComputeError]] = {
    compute_id: partial(_translate_declared_value_error, compute_id)
    for compute_id in value_error_declarations()
}


def _translate_value_error(compute_id: str, exc: ValueError) -> ComputeError:
    """invoke 中の ValueError を error.type へ翻訳する（§6.3.4 / §7.4）。

    指標固有の二意味 ValueError（message 照合を要する指標）は _VALUE_ERROR_TRANSLATORS へ
    登録された専用翻訳境界へ委譲する。未登録指標は一様に validation（汎用経路は指標名・
    日本語メッセージ片に依存しない）。
    """
    translator = _VALUE_ERROR_TRANSLATORS.get(compute_id)
    if translator is not None:
        translated = translator(exc)
    else:
        translated = ComputeError("validation", str(exc))
    # ISSUE-283: 指標が構造化診断（violations）を申告していれば、そのまま運ぶ。
    #   指標名で分岐しない（申告の有無だけを見る＝新しい指標が申告し始めても本関数は無改変）。
    declared = getattr(exc, "violations", None)
    if declared and not translated.violations:
        translated.violations = list(declared)
    return translated


def _translate_key_error(compute_id: str, exc: KeyError) -> ComputeError:
    """invoke 中の KeyError を error.type へ翻訳する（§7.4）。

    OHLC 列は compute 冒頭で確認済みのため、ここでの KeyError は time 必須指標での時刻解決
    失敗（missing_time）か、それ以外の列解決失敗（missing_column）。判定は _TABLE の per-指標
    宣言（``requires_time``）のみで、日本語メッセージ片には依存しない。
    """
    if requires_time(compute_id):
        return ComputeError("missing_time", str(exc))
    return ComputeError("missing_column", str(exc))


class IndicatorComputeAdapter:
    """IndicatorCallBinding ポートの実装（§3.3.1・§7.1.4）。"""

    def compute(
        self, compute_id: str, variant: str, df: Any, params: dict[str, Any]
    ) -> list[dict[str, Any]]:
        """既存 add_* を改変せず呼び、収集系列を系列 JSON で返す。例外翻訳＝§7.4。"""
        binding = CallBinding.resolve(compute_id, variant)

        # 決定論的な事前判別（KeyError/ValueError は型のみでは区別不能なため・§7.4）。
        if len(df) == 0:
            raise ComputeError("empty_series", "入力 OHLC が空です。")
        if not _has_columns(df):
            raise ComputeError("missing_column", "必須 OHLC 列が不足しています。")

        # line / histogram / horizontal_line を 1 指標内で併用する指標があるため統合 Fake を使う。
        # horizontal_line 群 payload の name は compute_id（price_range_power は従来同値）。
        fake = FakeChart(name=compute_id)
        try:
            binding.invoke(fake, df, params)
        except ImportError as exc:
            raise ComputeError("backend_unavailable", str(exc)) from exc
        except RuntimeError as exc:  # TgpBtlmFitter の tgp ロード失敗
            raise ComputeError("backend_unavailable", str(exc)) from exc
        except KeyError as exc:
            # OHLC は事前確認済み。KeyError の error.type 翻訳は翻訳境界へ委譲する。
            raise _translate_key_error(compute_id, exc) from exc
        except ValueError as exc:
            raise _translate_value_error(compute_id, exc) from exc

        return fake.to_payloads()
