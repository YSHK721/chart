"""台帳が宣言する実体の**所在だけ**を一時的に差し替える口（テストヘルパ・非テストモジュール）。

なぜ在るか（ISSUE-533 段階 3）:
    カタログが提供する系列は台帳 `marketdata/dataset_registry.py` の宣言から導かれる。
    よって「実体を小さな合成 CSV へ向けて測る」検定の継ぎ目も**台帳の宣言**である
    （以前はカタログのモジュール属性だった）。同じ 3 行を各検定が手書きで持つと、片方だけ
    直したときに別の系列を測り始める（複製は必ず取り残しを生む）。

    差し替えるのは ``path`` 欄ただ 1 つであり、他の欄（銘柄・木・基準・ベンダ・提供の宣言）は
    台帳の実物のままである——測りたいのは実体の内容であって、宣言の書き換えではない。

**テストではない**（ファイル名が test で始まらないため pytest は収集しない）。書込は行わない
（ファイルを書くのは呼出側の 「`tmp_path`」 の下）。
"""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from marketdata.dataset_registry import REGISTRY


def point_entity_at(monkeypatch, ref: str, path) -> Path:
    """台帳の ``ref`` の宣言のうち実体の所在だけを ``path`` へ向け、そのパスを返す。

    Args:
        monkeypatch: pytest の monkeypatch（``undo`` で元の宣言へ戻る）。
        ref: 台帳に在る datasetRef。無い ref は ``KeyError``（継ぎ目の不在がそのまま失敗
            として見える＝黙って新しい宣言を作らない）。
        path: 向ける先（``str`` でも ``Path`` でもよい）。

    Returns:
        向けた先の ``Path``。
    """
    entity = Path(path)
    monkeypatch.setitem(REGISTRY, ref, replace(REGISTRY[ref], path=entity))
    return entity
