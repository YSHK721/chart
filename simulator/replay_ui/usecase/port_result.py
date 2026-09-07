"""PortResult — 外部データ源 Port の応答を表す内側の値（ISSUE-502 段階 5B・DIP）。

## なぜ要るか（是正前の欠陥・実測）

replay_ports の 4 つの Port（MarketProfilePort / MarketProfileFormingPort /
TickvolProfilePort / CatalogPort）は戻り型が ``tuple[int, dict]`` ＝ **(HTTP ステータス, ボディ)**
だった。usecase 層の抽象定義が HTTP の語彙（ステータス番号）を持つと、次の 2 つが起きる:

* HTTP のエラー表現を変えると、**内側（usecase）の抽象定義の変更**になる。
  依存の向きが「内側 → 外側の技術」へ反転している（DIP 違反）。
* Port 実装（adapter）は「どの番号を返すか」まで決める責務を負う。同じ失敗に対して
  実装ごとに違う番号を返せてしまい、番号の一貫性を保証する場所がどこにも無い。

## 是正の形

Port は「成功したか」と「失敗ならどの**種別**か」だけを返す。番号は返さない。
種別（error_type）は正典表 api_shared.http_contract の ERROR_STATUS が鍵に持つ語彙
（validation / missing_column / missing_time / empty_series / backend_unavailable /
internal）で、これは HTTP ではなく**失敗の分類**である。分類 → HTTP ステータスの写像は
framework 層の写像関数 1 箇所だけが持つ。

``payload`` は成功・失敗のどちらでも**そのまま運ぶ**。運ぶだけで整形しないので、
外部（indicator_ui bridge）が組み立てたボディが 1 バイトも変わらずに front へ届く。

不変条件:
    * ``error_type is None`` ⇔ 成功。空文字は失敗種別として認めない（分類の欠落を
      成功として通さない）。
    * ``payload`` は dict。差し替えを防ぐため、生成後に本オブジェクトは変更できない
      （``frozen=True``。中身の dict まで凍結はしない——運ぶだけで触らないため）。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class PortResult:
    """Port の応答（成否の分類 ＋ そのまま運ぶボディ）。HTTP の語彙を持たない。

    Attributes:
        payload: 応答ボディ（成功・失敗のどちらでもそのまま運ぶ）。
        error_type: 失敗の分類。``None`` は成功。
    """

    payload: "dict[str, Any]"
    error_type: "str | None" = None

    def __post_init__(self) -> None:
        if not isinstance(self.payload, dict):
            raise TypeError(f"PortResult.payload は dict である必要があります: {type(self.payload)!r}")
        if self.error_type is not None and not (
            isinstance(self.error_type, str) and self.error_type
        ):
            raise ValueError(
                "PortResult.error_type は None（成功）か非空の分類文字列である必要があります: "
                f"{self.error_type!r}"
            )

    @property
    def ok(self) -> bool:
        """成功なら True（分類が付いていない）。"""
        return self.error_type is None

    @classmethod
    def success(cls, payload: "dict[str, Any]") -> "PortResult":
        """成功の応答を作る。"""
        return cls(payload=payload)

    @classmethod
    def failure(cls, error_type: str, payload: "dict[str, Any]") -> "PortResult":
        """失敗の応答を作る（``error_type`` は失敗の分類・HTTP 番号ではない）。"""
        return cls(payload=payload, error_type=error_type)
