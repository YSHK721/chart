"""IndicatorComputeAdapter（内部設計書 §3.3.1）— 既存 add_* 隔離点（唯一）。

FakeChart を生成 → CallBinding で実 add_* を呼出 → FakeChart 収集結果を系列 JSON
（§6.3.2 / §6.3.3）へ変換する。例外は §6.3.4 / §7.4 の error.type へ翻訳する。

既存 src は read-only import（改変禁止）。描画ライブラリは import しない。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from adapter.compute.call_binding import (
    CallBinding,
    profit_band_empty_bucket_error,
    requires_time,
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
    """§6.3.4 計算 API エラー。``error_type`` は翻訳済みの type。"""

    error_type: str
    message: str

    def __str__(self) -> str:  # pragma: no cover - 表示補助
        return f"{self.error_type}: {self.message}"


def _has_columns(df: Any) -> bool:
    cols = {str(c).lower() for c in df.columns}
    return all(c in cols for c in _REQUIRED_COLUMNS)


# =========================================================================== #
# profit_band 専用の例外翻訳境界（ISSUE-098 🟡-5・LSP 是正 LSP-3）
#
# profit_band は「必須バケット空」(empty_series) と「normalize 不正等」(validation) を区別して
# 翻訳する必要がある。従来は双方が素の ``ValueError`` だったため日本語メッセージ片 "バケット" を
# 照合していたが、profit_band src に専用型 ``EmptyBucketError``（ValueError サブクラス・後方互換）
# を導入し、本境界は *型* で識別する（bands.build_bands(require_full=True)→EmptyBucketError・
# profit_band/src/bands.py／normalize 不正は素の ValueError・robust_bands.py:140）。
# メッセージ片への依存を排し、送出条件・ユーザ向けメッセージ・HTTP ステータスは従来と同一。
#
# LSP 是正: この profit_band 固有知識（指標名 "profit_band"・型 EmptyBucketError）を本境界 1 箇所に
# 閉じ込め、汎用計算経路（compute / _translate_value_error）からは指標名も型も参照しない。汎用経路は
# _VALUE_ERROR_TRANSLATORS への登録有無だけで一様に扱う。なお「空入力」由来の ValueError（core.py の
# "空です"）は compute 冒頭の ``len(df) == 0`` pre-check で確定済みのため invoke 後には到達しない。


def _translate_profit_band_value_error(exc: ValueError) -> ComputeError:
    """profit_band の ValueError を二意味（empty_series / validation）へ翻訳する。

    型識別（``EmptyBucketError``）はこの境界の内側にのみ存在する。EmptyBucketError は
    ValueError サブクラスのため、既存の except ValueError 経路（compute）はそのまま捕捉する。
    """
    if isinstance(exc, profit_band_empty_bucket_error()):
        return ComputeError("empty_series", str(exc))
    return ComputeError("validation", str(exc))


# compute_id → ValueError 専用翻訳器。「型で識別不能な二意味 ValueError」を持つ指標のみを
# 登録する（現状 profit_band のみ）。未登録指標は汎用 validation へ一様翻訳される。
_VALUE_ERROR_TRANSLATORS: dict[str, Callable[[ValueError], ComputeError]] = {
    "profit_band": _translate_profit_band_value_error,
}


def _translate_value_error(compute_id: str, exc: ValueError) -> ComputeError:
    """invoke 中の ValueError を error.type へ翻訳する（§6.3.4 / §7.4）。

    指標固有の二意味 ValueError（message 照合を要する指標）は _VALUE_ERROR_TRANSLATORS へ
    登録された専用翻訳境界へ委譲する。未登録指標は一様に validation（汎用経路は指標名・
    日本語メッセージ片に依存しない）。
    """
    translator = _VALUE_ERROR_TRANSLATORS.get(compute_id)
    if translator is not None:
        return translator(exc)
    return ComputeError("validation", str(exc))


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
