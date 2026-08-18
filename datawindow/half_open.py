"""取得窓 ``[start, end)`` の境界正規化と半開判定の**唯一の実体**（ISSUE-401 🟡-2）。

1. 層名/責務:
    中立共有層。窓境界（datetime）を epoch 秒へ正規化し、ある時刻が半開区間
    ``[start, end)`` に入るかを判定する。読み取り I/O・列マッピング・Bar/Candle の
    写像は一切持たない（それらは各 adapter の責務）。

2. 含む構造:
    epoch_seconds_of_datetime : datetime → epoch 秒（UTC 基準）。
    HalfOpenEpochWindow       : epoch 秒で表した半開窓。``contains`` が唯一の述語。

3. 依存:
    標準: dataclasses / datetime
    外部: なし（numpy / pandas を import しない。domain 層から読んでも汚染しない）
    プロジェクト内: なし（`simulator` / `marketdata` のどちらにも依存しない）

4. naive datetime の扱い = **UTC とみなす**（3 択のうちの確定・根拠は実測と既存合意）:
    - 「ローカル TZ とみなす」は棄却する。それが**原因そのもの**である。
      実測（是正前・``TZ=Asia/Tokyo``・naive ``datetime(2025, 1, 10)``）::

          Bar 段    epoch_seconds(dt)    = 1736467200   （UTC 解釈）
          Candle 段 int(dt.timestamp())  = 1736434800   （ローカル TZ 解釈）
                                            差 = 32400 秒

      同じ窓指定でも実行環境の TZ で選択される足が変わる＝バックテストが再現しない。
    - 「naive を拒否する」も棄却する。既存合意
      （``simulator/tests/unit/test_bar_time_epoch.py`` の
      ``test_naive_datetime_is_interpreted_as_utc``）が naive = UTC を既に固定しており、
      その合意は本件の欠陥ではない。欠陥は「Candle 段がその合意に従っていないこと」で
      あるため、是正は合意の**適用範囲を揃えること**であって合意の変更ではない。
      加えて `bar.time` にも同じ変換器を用いる（規則を 2 つ持たない）ため、拒否へ倒すと
      `bar.time` 側の既存契約まで狭めることになる。
    - 「UTC とみなす」を採る。変換結果が入力値だけの関数になり、``time.tzname`` を
      参照しなくなる＝**環境依存という原因を除去**する（naive を「来ないことにする」
      症状回避ではない。naive は受理され、決定論的な 1 つの意味を持つ）。
    - 内部設計 §8.4 / ``main/tester_settings/window.py`` W-3 の「境界時刻はすべて UTC
      aware」という規定とも整合する（aware 入力では本関数は自身の offset を使うため、
      規定を守っている呼出側の結果は 1 bit も変わらない＝byte 等価）。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone


def epoch_seconds_of_datetime(value: datetime) -> int:
    """datetime → epoch 秒（int・UTC 基準）。

    事前条件: ``value`` は ``datetime``（aware / naive のいずれでもよい）。
    事後条件: aware は自身の offset で、naive は **UTC** とみなして換算した epoch 秒を
        返す。返り値はプロセスのローカル TZ に依存しない（``time.tzname`` を参照しない）。
    例外: なし（型が ``datetime`` でない場合の判定は呼出側の責務）。
    """
    aware = value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
    return int(aware.timestamp())


@dataclass(frozen=True)
class HalfOpenEpochWindow:
    """epoch 秒で表した半開窓 ``[start, end)``。

    不変条件: ``start`` / ``end`` は epoch 秒（int）。``start > end`` は空窓として扱う
        （``contains`` が常に ``False`` を返す）。窓の妥当性検査は呼出側の責務であり、
        本型は判定規則のみを持つ（推測で境界を入れ替えない）。
    """

    start: int
    end: int

    @classmethod
    def from_datetimes(cls, start: datetime, end: datetime) -> "HalfOpenEpochWindow":
        """datetime の境界対から生成する（正規化は ``epoch_seconds_of_datetime``）。"""
        return cls(epoch_seconds_of_datetime(start), epoch_seconds_of_datetime(end))

    def contains(self, epoch: int) -> bool:
        """``epoch`` が ``[start, end)`` に入るか（半開判定の唯一の実体）。"""
        return self.start <= epoch < self.end
