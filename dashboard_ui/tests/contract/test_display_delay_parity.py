"""ISSUE-502 D-8: 表示遅延（12 秒）が front の唯一源と一致していることを固定する。

方向: 遅延の定義を持つのはライブ front の `LiveTickPlayer`（`DELAY_MS`）ただ 1 つ。
値は「poll 間隔 + feed lag + fetch」の実測から決まる front の都合であり、
ダッシュボードはそれに**追従する側**である（`DISPLAY_DELAY_SECONDS`）。したがって
追従側であるダッシュボードが、依存先の宣言を読んで一致を主張する
（依存の向きと検定の向きを揃える＝唯一源は自分の追従者を知らない）。

なぜ必要か（精査台帳 .doc/solid_audit_20260906.md D-8 の実測）:
    `DISPLAY_DELAY_SECONDS = 12` は `DELAY_MS = 12000` の手写しで、コメント自身が
    「変えるときは両方」と人手同期を宣言していた（＝機械的検査 0）。片方だけ変えると
    「印はサーバの実勢・表示は front の遅延」の 2 つの時計が混在し、跨ぎの発光と
    表示が最大 12 秒ずれる。ずれても例外は出ず表示も続くため、出力の検査では
    原理的に落ちない（ISSUE-254 と同型）。

言語が違うため値そのものは共有できない。共有できるのは**宣言の所在**なので、
front の `DELAY_MS` を export（公開面）にし、本検定がその宣言を読んで突き合わせる。
"""
from __future__ import annotations

import re
from pathlib import Path

from dashboard_ui.adapter.gateway.indicator_ui_compute_gateway import (
    DISPLAY_DELAY_SECONDS,
)

#: `test_display_delay_parity.py` → contract → tests → dashboard_ui → リポジトリ根。
_REPO_ROOT = Path(__file__).resolve().parents[3]

#: 遅延の唯一源（ライブ front の再生プレイヤー）。
_LIVE_TICK_PLAYER = (
    _REPO_ROOT / "indigators" / "indicator_ui" / "web" / "js" / "adapter" / "front"
    / "live_tick_player.js"
)

#: 唯一源の宣言。形が変わったら「別の値と一致」ではなく**不在**で落とす。
_DECL = re.compile(r"export\s+const\s+DELAY_MS\s*=\s*(\d+)\s*;")


def _delay_ms_of_the_live_player() -> int:
    assert _LIVE_TICK_PLAYER.exists(), f"唯一源が見つかりません: {_LIVE_TICK_PLAYER}"
    match = _DECL.search(_LIVE_TICK_PLAYER.read_text(encoding="utf-8"))
    assert match is not None, (
        f"{_LIVE_TICK_PLAYER.name} に `export const DELAY_MS = <数値>;` が見つかりません。"
        "遅延の唯一源は本宣言であり、ダッシュボードはこれを読んで追従する"
        "（名前・形を変えるなら dashboard_ui 側の追従先も同時に直すこと）"
    )
    return int(match.group(1))


def test_the_display_delay_follows_the_live_player() -> None:
    """ダッシュボードの巻き戻し秒数が front の固定遅延と同じ時点を指す。"""
    assert DISPLAY_DELAY_SECONDS * 1000 == _delay_ms_of_the_live_player(), (
        "表示遅延が front の唯一源と食い違っています"
        "（印と表示で 2 つの時計が混在し、跨ぎの発光が最大この差だけずれる）"
    )


def test_the_reader_is_not_vacuous() -> None:
    """読み取り規則の生存確認（0 や None との一致で恒真化していない）。"""
    assert _delay_ms_of_the_live_player() > 0
    assert _DECL.search("export const DELAY_MS = 1;") is not None
    assert _DECL.search("const DELAY_MS = 1;") is None, (
        "非公開宣言を拾ってしまう（export＝公開面であることを検定が要求していない）"
    )
