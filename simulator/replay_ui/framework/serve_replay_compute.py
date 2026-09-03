"""ReplayComputeApp — POST /compute のモード表と例外分類（ISSUE-479 Wave2 3-5 / S-3）。

分割前の do_POST は 3 つのモード（既定 / latest_seq / latest_seq_multi）それぞれに
``except MemoryError / except ValueError / except Exception`` を書き下しており、**同一の
分類が 9 ブロックに複製**されていた。分類を 1 つ直すには 3 箇所を同じ理由で触ることになり、
片方だけ直す形の壊れ方を招く（ISSUE-097 で実際に起きたのがこの形である）。

モードごとの差は 2 つしかない:
    * どの入口を呼ぶか（compute / compute_seq / compute_seq_multi）
    * 応答のどのキーへ載せるか（series / steps / results）

その 2 つだけを表で宣言し、分類・世代の引き回し・応答の組み立ては 1 組だけ書く。
分類の中身（どの例外がどの status とどの type になるか）は持たない——それは中央翻訳器
``serve_replay._error_response`` の単一ソースである。本モジュールが持つのは
「モード固有のメッセージ（MemoryError→"memory limit"・generic→"Name: msg"）を供給する」
ことだけで、これは分割前の except ブロックが供給していたものと 1 文字も変わらない。

応答 byte のパリティは
``replay_ui/tests/integration/test_replay_route_parity.py`` が 3 モード × 正常/異常で固定する。
"""
from __future__ import annotations

from typing import Any

from simulator.replay_ui.framework.serve_replay import _error_response

#: POST /compute のモード表: mode → (App の入口メソッド名, 応答のキー)。
#:   **拡張点ではない**。モードを増やすときは、front と back の契約を変えたということなので、
#:   ここへ 1 行足すのと同時に応答キーの意味を決める（キーが重ならないことは検定が固定する）。
_COMPUTE_POST_MODES: "dict[str, tuple[str, str]]" = {
    "latest_seq_multi": ("compute_seq_multi", "results"),
    "latest_seq": ("compute_seq", "steps"),
}

#: 表に無いモード（``None`` / ``"latest"`` / 未知の文字列）が落ちる先。分割前の do_POST も
#:   未知モードを弾かずここへ落としていた（挙動不変）。
_DEFAULT_COMPUTE_MODE: "tuple[str, str]" = ("compute", "series")


class ReplayComputeApp:
    """POST /compute を 1 つの経路で処理する App（モードの差は表引きだけ）。

    ``inner``: 3 つの入口（compute / compute_seq / compute_seq_multi）を持つ App。
    """

    def __init__(self, *, inner: Any) -> None:
        self._inner = inner

    @property
    def inner(self) -> Any:
        """包んでいる内側 App（結線を複製していないことを確かめる面）。"""
        return self._inner

    def respond(self, body: dict) -> "tuple[int, Any]":
        """``(status, payload)`` を返す（書き出しは呼び出し側の単一定義が行う）。"""
        generation = body.get("generation", 0)
        method, key = _COMPUTE_POST_MODES.get(
            body.get("mode"), _DEFAULT_COMPUTE_MODE
        )
        try:
            result = getattr(self._inner, method)(body)
        # 分類（status/type）は _error_response へ集約（ISSUE-097 🟡-4）。ここが供給するのは
        #   モード共通のメッセージだけである（分割前の except ブロックと 1 文字も変わらない）。
        except MemoryError as e:
            return _error_response(e, generation=generation, message="memory limit")
        except ValueError as e:
            return _error_response(e, generation=generation)
        except Exception as e:  # noqa: BLE001
            return _error_response(
                e, generation=generation, message=f"{type(e).__name__}: {str(e)[:200]}"
            )
        return (200, {"ok": True, "generation": generation, key: result})

    def __getattr__(self, name: str) -> Any:
        """自分が持たない属性は内側 App へ委譲する（結線を殺さない）。"""
        inner = self.__dict__.get("_inner")
        if inner is None:  # __init__ 完了前・複製時の再帰防止
            raise AttributeError(name)
        return getattr(inner, name)
