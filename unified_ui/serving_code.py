#!/usr/bin/env python3
"""配信中のコードの同一性（ISSUE-531）— 起動時に読んだ Python ソースと、今のツリーを照合する。

なぜ要るか:
    ``/__serving_root``（ISSUE-348）は「どのツリーが配信しているか」しか答えない。同じツリーで
    コードを更新しても Python は起動時のまま動き（静的ファイルだけは要求ごとに読み直される）、
    unified_ui/serve.sh は「既に起動済みです」で exit 0 していた。画面の一部だけ新しく、サーバ側の判断は
    古いまま動く（2026-09-25 実測: 稼働中の sim core が古いモジュールを保持し datasets が 1 件）。

「同じコード」の定義（判断の根拠）:
    - **Python ソースの内容**で決める。起動時に読まれ、更新が反映されないのは Python だけである
      （ISSUE-531 の実測: 静的ファイルは要求ごとに読み直され新しかった）。対象の宣言は
      :data:`SOURCE_PATHSPEC` の 1 か所である。
    - 内容は git の blob ハッシュで表す。**HEAD・コミット時刻は使わない**: 文書だけのコミットや、
      変更をコミットしただけでは内容は変わらないのに、HEAD は動く（再起動を求める誤報になる）。
    - **時刻は判定に使わない**: 更新時刻は内容を変えない操作（touch・同じ内容への書き戻し）でも
      動き、内容が変わっても保たれうる。時刻は「違う」と判定した後に**どれが変わったか**を
      示す説明にだけ使う。
    - 未コミットの変更・未追跡の Python ファイル・削除は内容の差として数える（起動時に読まれる）。
      ``.gitignore`` で無視されたファイルは数えない（``.claude/worktrees`` 等の複製を含む）。

申告の形（``/__serving_root`` と同じく平文 1 行）:
    ``<指紋 sha256> <申告を作った時刻（epoch 秒）>``。serve.sh が **core を起動する前**に作り、
    router が受け取って ``/__serving_code`` で配る。core 起動より前に作るので、作った後に更新された
    ファイルは「違う」側へ倒れる（古いものを「同じ」と言う向きの誤りが起きない）。

計算量: git の呼び出しは定数回（ファイル数に比例しない）。内容を読む（``hash-object``）のは
    index と食い違うファイルと未追跡のファイルだけで、変わっていないファイルは git の index が持つ
    ハッシュを使う。観測の境界は ``code_manifest`` の ``git`` 引数と ``compare`` の ``mtime`` 引数。

標準ライブラリだけで書く（router は venv でない python3 で起動される）。
"""
from __future__ import annotations

import datetime as dt
import hashlib
import os
import subprocess
import sys
import time
from typing import Callable, Dict, List, NamedTuple, Optional, Sequence

#: 同一性の対象（git の pathspec）。起動時に読まれて更新が反映されないもの＝Python ソース。
SOURCE_PATHSPEC = ("*.py",)

#: 違いの説明に並べる件数の上限（ブランチ切替で数百件になっても表示を溢れさせない）。
_LIST_LIMIT = 20

GitRunner = Callable[[Sequence[str], Optional[bytes]], bytes]


def run_git(repo_root) -> GitRunner:
    """``repo_root`` で git を走らせる実体（標準出力を bytes で返す）。index も作業ツリーも書き換えない呼び出しだけに使う。"""
    root = str(repo_root)

    def run(args: Sequence[str], stdin: Optional[bytes] = None) -> bytes:
        return subprocess.run(
            ["git", "-C", root, *args], input=stdin, capture_output=True, check=True
        ).stdout

    return run


def _paths(raw: bytes) -> List[str]:
    return [p for p in raw.decode("utf-8", "surrogateescape").split("\0") if p]


def code_manifest(git: GitRunner) -> Dict[str, str]:
    """今のツリーの Python ソース → blob ハッシュ（未コミットの変更・未追跡を含み、削除を除く）。"""
    spec = ["--", *SOURCE_PATHSPEC]
    manifest: Dict[str, str] = {}
    for entry in _paths(git(["ls-files", "-s", "-z", *spec], None)):
        meta, path = entry.split("\t", 1)
        manifest[path] = meta.split()[1]
    deleted = set(_paths(git(["ls-files", "-d", "-z", *spec], None)))
    modified = set(_paths(git(["ls-files", "-m", "-z", *spec], None))) - deleted
    untracked = set(_paths(git(["ls-files", "-o", "--exclude-standard", "-z", *spec], None)))
    for path in deleted:
        manifest.pop(path, None)
    to_hash = sorted(modified | untracked)
    if to_hash:
        out = git(["hash-object", "--stdin-paths"], "\n".join(to_hash).encode("utf-8"))
        for path, sha in zip(to_hash, out.decode("ascii").split()):
            manifest[path] = sha
    return manifest


def fingerprint(manifest: Dict[str, str]) -> str:
    """目録の指紋（パスの順に依らない）。"""
    digest = hashlib.sha256()
    for path in sorted(manifest):
        digest.update(path.encode("utf-8", "surrogateescape") + b"\0" + manifest[path].encode() + b"\n")
    return digest.hexdigest()


def identity_line(git: GitRunner, *, now: float) -> str:
    """申告 1 行（``<指紋> <epoch 秒>``）。"""
    return f"{fingerprint(code_manifest(git))} {now:.3f}"


def parse_identity_line(line: str):
    """申告 1 行 → (指紋, epoch 秒)。読めなければ ValueError（判定不能を「同じ」に倒さない）。"""
    fields = line.split()
    if len(fields) != 2 or len(fields[0]) != 64:
        raise ValueError(f"申告を読めません: {line!r}")
    int(fields[0], 16)
    return fields[0], float(fields[1])


class Comparison(NamedTuple):
    same: bool
    served: str
    current: str
    started: float
    newer: List[str]


def compare(repo_root, git: GitRunner, served_line: str, *,
            mtime: Callable[[str], float] = os.path.getmtime) -> Comparison:
    """申告と今のツリーを照合する。違うときだけ、起動後に更新されたファイル（目録の中だけ）を数える。"""
    served, started = parse_identity_line(served_line)
    manifest = code_manifest(git)
    current = fingerprint(manifest)
    if current == served:
        return Comparison(True, served, current, started, [])
    newer = [p for p in sorted(manifest) if mtime(os.path.join(str(repo_root), p)) > started]
    return Comparison(False, served, current, started, newer)


def describe(result: Comparison) -> str:
    """違いの説明（人が読む）。"""
    started = dt.datetime.fromtimestamp(result.started).strftime("%Y-%m-%d %H:%M:%S")
    lines = [
        "配信中のコードは、このツリーの今の Python ソースと一致しません。",
        f"  起動時: {started}（指紋 {result.served[:12]}）",
        f"  現在  : 指紋 {result.current[:12]}",
    ]
    if result.newer:
        lines.append(f"  起動後に更新された Python ファイル（{len(result.newer)} 件）:")
        lines += [f"    {p}" for p in result.newer[:_LIST_LIMIT]]
        if len(result.newer) > _LIST_LIMIT:
            lines.append(f"    ほか {len(result.newer) - _LIST_LIMIT} 件")
    else:
        lines.append("  起動後の更新時刻を持つファイルはありません（削除、または更新時刻を保つ操作による差です）。")
    return "\n".join(lines)


def main(argv: Sequence[str]) -> int:
    """``identity <root>`` → 申告 1 行を出す。``compare <root> <申告>`` → 0 同じ / 1 違う / 2 読めない。"""
    if len(argv) == 2 and argv[0] == "identity":
        print(identity_line(run_git(argv[1]), now=time.time()))
        return 0
    if len(argv) == 3 and argv[0] == "compare":
        try:
            result = compare(argv[1], run_git(argv[1]), argv[2])
        except ValueError as exc:
            print(str(exc))
            return 2
        if result.same:
            return 0
        print(describe(result))
        return 1
    print("使い方: serving_code.py identity <repo_root> | compare <repo_root> <申告>", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
