"""ファイルを開く口と、そこから呼び手へ**配られた量**を数える Test Spy（テストヘルパ・非テストモジュール）。

なぜ在るか（「開いた回数」では捕まらない退化・実測 2026-09-25・本作業ツリー・HEAD 14fc9a13）:
    計算量検定が「開いた回数」だけを数えていると、**1 回の open の中で読む量が O(n) になる
    退化**を 1 ビットも検出しない。実測: `simulator.sim_ui.adapter.symbol_spec_catalog` の
    末尾読取（終端から定数窓だけ後読みする 3 行）を、先頭へ位置付け直して全部読む形へ
    退化させた状態で
    `simulator/tests/unit/test_symbol_spec_catalog_ledger_wiring.py` と
    `simulator/tests/unit/test_symbol_spec_catalog_spread_axis.py` を走らせると
    **20 passed のまま素通しした**（開いた数も、出力である日付トークンも 1 ビットも変わらない
    ため。壁時計は 0.46s → 10.91s だが、**時間は表明しない**——マシン負荷で揺れる閾値は
    緩んで浪費を通す）。

    したがって継ぎ目を「開いた回数」から**読取の発行と配られた量**へ移す。open を包むだけ
    でなく、**返ってきたファイルそのものを包む**必要がある——読取は open の外側からは
    見えないからである。

包む名前が 2 つ要る（実測 2026-09-25・本環境 CPython 3.13.5）:
    `io.open` と `builtins.open` は同じ関数実体だが（``io.open is builtins.open`` は True）、
    参照する名前空間が違う。pathlib の口（Path.open / Path.read_text）は
    `io` の名前を引き、ヘッダ 1 行だけを読む口
    （`simulator.adapter.repository.ohlc_marketdata_csv` の同名関数）は組込みの名前を引く。
    片方だけを包むと他方を取り逃す。両方を同じ包みへ差し替える。

数え方（このモジュールが唯一の宣言）:
    配られた量 … 読取の口（read / readline / readlines / 反復）が**呼び手へ返した**長さの総和。
                  単位はモードに従う（バイナリはバイト数・テキストは文字数）。
    読取の発行 … 同じ口の呼出回数。
    位置付け   … seek の呼出回数（行ごとに位置付け直す退化を数える）。
    いずれも**実体ごとに分けて**数える（``delivered(path)`` のように問える）。
    **回数そのものを期待値に焼き込まない**のは呼出側の責務である（焼き込むと浪費が仕様へ昇格する）。

既存の先行実装との関係（取り残しの申し送り）:
    `simulator/tests/unit/test_ohlc_marketdata_csv_supplies_spread.py` は、ヘッダ 1 行だけを
    読む口について同型の覆いを私有で持っている（read / readline の 2 口・実体ごとに分けない
    版）。本モジュールはその一般化だが、**移行は本段（ISSUE-511 段階 8-D-4）の範囲外**であり
    当該ファイルには 1 バイトも触れていない（範囲外の既存検定を触るには承認が要る）。

本モジュールは既定のデータ木を読み書きしない（何も開かない・数えるだけである）。
**テストではない**（ファイル名が test で始まらないため pytest は収集しない）。
"""
from __future__ import annotations

import builtins
import io


class FileReads:
    """開いた口・読取の発行・配られた量・位置付けの記録。

    属性:
        opened      open へ渡された実体を発行順に 1 件ずつ。
        deliveries  （実体, 読取の口の名, 配られた量）を発行順に 1 件ずつ。
        seeks       （実体, seek の引数）を発行順に 1 件ずつ。
    """

    def __init__(self) -> None:
        self.opened: list = []
        self.deliveries: "list[tuple[str, str, int]]" = []
        self.seeks: "list[tuple[str, tuple]]" = []

    def _matches(self, path, recorded: str) -> bool:
        return path is None or recorded == str(path)

    def delivered(self, path=None) -> int:
        """呼び手へ配られた量の合計（``path`` を与えるとその実体の分だけ）。"""
        return sum(n for p, _how, n in self.deliveries if self._matches(path, p))

    def reads(self, path=None) -> int:
        """読取の発行回数（同上）。"""
        return sum(1 for p, _how, _n in self.deliveries if self._matches(path, p))

    def seek_count(self, path=None) -> int:
        """位置付けの発行回数（同上）。"""
        return sum(1 for p, _args in self.seeks if self._matches(path, p))


class _RecordedFile:
    """開いたファイルの覆い。読取と位置付けを記録し、それ以外の操作は中身へそのまま委ねる。

    委ねるのを既定にするのは、数えない操作（tell / close / write / mode 等）を**列挙しない**
    ためである。列挙すると、被検査側が別の口を使い始めたときに覆いが AttributeError で
    落ち、「失敗」ではなく「エラー」になる。
    """

    def __init__(self, inner, path: str, log: FileReads) -> None:
        self._inner = inner
        self._path = path
        self._log = log

    def __getattr__(self, name):
        return getattr(self._inner, name)

    def __enter__(self):
        self._inner.__enter__()
        return self          # 覆いを返す（中身を返すと with の内側の読取を取り逃す）

    def __exit__(self, *exc):
        return self._inner.__exit__(*exc)

    def __iter__(self):
        return self

    def __next__(self):
        return self._counted("next", next(self._inner))

    def read(self, *args, **kwargs):
        return self._counted("read", self._inner.read(*args, **kwargs))

    def readline(self, *args, **kwargs):
        return self._counted("readline", self._inner.readline(*args, **kwargs))

    def readlines(self, *args, **kwargs):
        lines = self._inner.readlines(*args, **kwargs)
        for line in lines:
            self._log.deliveries.append((self._path, "readlines", len(line)))
        return lines

    def seek(self, *args, **kwargs):
        self._log.seeks.append((self._path, args))
        return self._inner.seek(*args, **kwargs)

    def _counted(self, how: str, chunk):
        self._log.deliveries.append((self._path, how, len(chunk)))
        return chunk


def spy_file_reads(monkeypatch) -> FileReads:
    """これ以降に開かれた実体と、その口から配られた量を記録する Test Spy を仕掛ける。

    記録は返した :class:`FileReads` に溜まる。差し替えは ``monkeypatch`` の寿命で戻る
    （``monkeypatch.undo()`` を呼べばその時点で戻る）。
    """
    log = FileReads()
    real = builtins.open

    def recorded(file, *args, **kwargs):
        log.opened.append(file)
        return _RecordedFile(real(file, *args, **kwargs), str(file), log)

    monkeypatch.setattr(builtins, "open", recorded)
    monkeypatch.setattr(io, "open", recorded)
    return log
