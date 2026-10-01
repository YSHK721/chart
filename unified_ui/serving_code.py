#!/usr/bin/env python3
"""配信中のコードの同一性（ISSUE-531）— 起動時に読んだ配信の入力と、今のツリーを照合する。

なぜ要るか:
    ``/__serving_root``（ISSUE-348）は「どのツリーが配信しているか」しか答えない。同じツリーで
    コードを更新しても起動時に読んだもの（Python・起動時に 1 回読む台帳等）は起動時のまま動き、
    unified_ui/serve.sh は「既に起動済みです」で exit 0 していた。画面の一部だけ新しく、サーバ側の判断は
    古いまま動く（2026-09-25 実測: 稼働中の sim core が古いモジュールを保持し datasets が 1 件）。

「同じコード」の定義（判断の根拠）:
    - 対象は**ツリーのすべて − 配信プロセスが読まないと宣言したもの**（:data:`NOT_READ_BY_SERVING`）。
      拡張子の許可リストにしないのは、誤りの向きを安全側に倒すためである。「同じ」と誤ると古いコードを
      黙って配信する（ISSUE-531 そのもの）が、「違う」と誤っても不要な --restart で済む。
      Python 以外にも起動時に 1 回だけ読まれる入力がある（独立レビュー 2026-10-01 で再現:
      common/core_web_topology.json は core_web_topology.py の lru_cache と import 時の決定で
      起動時のまま・各 core の serve.sh・tools/dev_paths.sh）。宣言に無いものは含める側に倒す。
    - ``.gitignore`` の対象は数えない（data・venv・``.claude/worktrees`` の複製）。ただし起動時に
      source される dev_paths.local.sh は明示して数える（:data:`IGNORED_STARTUP_INPUTS`）。
      内容はハッシュとしてだけ扱い、値は出力しない（秘密を含む）。
    - 内容は git の blob ハッシュで表す。**HEAD・コミット時刻は使わない**: 変更をコミットしただけでは
      内容は変わらないのに HEAD は動く（再起動を求める誤報になる）。
    - **時刻は判定に使わない**: 更新時刻は内容を変えない操作でも動き、内容が変わっても保たれうる。
      時刻は「違う」と判定した後に**どれが変わったか**を示す説明にだけ使う。
    - 未コミットの変更・未追跡・削除は内容の差として数える。

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

#: 配信プロセスが読まないと宣言したもの（git の pathspec・glob）。ここに無いものはすべて対象に含める。
#: 理由のない除外を足さない（足すと「同じ」と誤る向きの穴になる）。
NOT_READ_BY_SERVING = (
    # テスト: pytest / vitest だけが読む。
    "**/tests/**",
    "**/test_*.py",
    "**/conftest.py",
    # 作業者の設定とその複製（.claude/worktrees は .gitignore 済みだが追跡分もある）。
    ".claude/**",
    # 文書。
    ".doc/**",
    "docs/**",
    "**/*.md",
    # 試作（配信の構成に入っていない）。
    "prototype_*/**",
    # 要求ごとに読み直される静的ファイル。実測（2026-09-25・ISSUE-531）で配信中の sim の js は
    #   新しかった。コード: 各 core の静的配信は要求ごとに読む（simulator/replay_ui/framework/
    #   static_file_server.py の read_bytes を sim・dashboard が共有・indigators/indicator_ui/api/
    #   framework/server.py の _handle_static・unified_ui/router.py の _serve_static）。
    #   web 配下でも json 等は起動時に読まれうるので含める（拡張子を絞る）。含めるのは追跡済みと、
    #   .gitignore の対象でない未追跡に限る（未追跡の扱いは .gitignore に従う）。
    "**/web/**/*.js",
    "**/web/**/*.mjs",
    "**/web/**/*.css",
    "**/web/**/*.html",
)

#: .gitignore の対象のうち、起動時に読まれるもの（tools/dev_paths.sh が source する）。
IGNORED_STARTUP_INPUTS = ("dev_paths.local.sh",)

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

    run.root = root  # type: ignore[attr-defined]  # symlink の判定に使う（code_manifest）
    return run


def _blob_sha(data: bytes) -> str:
    """git の blob ハッシュ（``git hash-object`` と同じ値）。"""
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


def _paths(raw: bytes) -> List[str]:
    return [p for p in raw.decode("utf-8", "surrogateescape").split("\0") if p]


def code_manifest(git: GitRunner) -> Dict[str, str]:
    """今のツリーの配信の入力 → blob ハッシュ（未コミットの変更・未追跡を含み、削除を除く）。"""
    spec = ["--", ".", *(f":(exclude,glob){p}" for p in NOT_READ_BY_SERVING)]
    manifest: Dict[str, str] = {}
    for entry in _paths(git(["ls-files", "-s", "-z", *spec], None)):
        meta, path = entry.split("\t", 1)
        manifest[path] = meta.split()[1]
    deleted = set(_paths(git(["ls-files", "-d", "-z", *spec], None)))
    modified = set(_paths(git(["ls-files", "-m", "-z", *spec], None))) - deleted
    untracked = set(_paths(git(["ls-files", "-o", "--exclude-standard", "-z", *spec], None)))
    untracked |= set(_paths(git(
        ["ls-files", "-o", "-i", "--exclude-standard", "-z", "--", *IGNORED_STARTUP_INPUTS], None)))
    for path in deleted:
        manifest.pop(path, None)
    to_hash = sorted(modified | untracked)
    # symlink はリンク先の文字列を blob としてハッシュする（git の mode 120000 と同じ扱い）。
    #   hash-object --stdin-paths はリンクを辿るため、ディレクトリを指すと失敗する
    #   （.gitignore の `venv/` は symlink に効かず、未追跡として目録に入る・独立レビューで再現）。
    root = getattr(git, "root", None)
    if root is not None:
        links = {p for p in to_hash if os.path.islink(os.path.join(root, p))}
        for path in links:
            manifest[path] = _blob_sha(os.fsencode(os.readlink(os.path.join(root, path))))
        to_hash = [p for p in to_hash if p not in links]
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
        "配信中のコードは、このツリーの今の内容と一致しません。",
        f"  起動時: {started}（指紋 {result.served[:12]}）",
        f"  現在  : 指紋 {result.current[:12]}",
    ]
    if result.newer:
        lines.append(f"  起動後に更新されたファイル（{len(result.newer)} 件）:")
        lines += [f"    {p}" for p in result.newer[:_LIST_LIMIT]]
        if len(result.newer) > _LIST_LIMIT:
            lines.append(f"    ほか {len(result.newer) - _LIST_LIMIT} 件")
    else:
        lines.append("  起動後の更新時刻を持つファイルはありません（削除、または更新時刻を保つ操作による差です）。")
    return "\n".join(lines)


def main(argv: Sequence[str]) -> int:
    """``identity <root>`` → 申告 1 行を出す。``compare <root> <申告>`` → 0 同じ / 1 違う / 2 読めない。"""
    if len(argv) == 2 and argv[0] == "identity":
        try:
            print(identity_line(run_git(argv[1]), now=time.time()))
        except (OSError, subprocess.SubprocessError, ValueError) as exc:
            # 申告を作れない＝次の serve.sh は「確かめられない」になる（serve.sh が空として扱う）。
            print(f"同一性の申告を作れません: {exc}", file=sys.stderr)
            return 2
        return 0
    if len(argv) == 3 and argv[0] == "compare":
        try:
            result = compare(argv[1], run_git(argv[1]), argv[2])
        except (ValueError, OSError, subprocess.SubprocessError) as exc:
            # 申告が読めない・git が失敗した等は「確かめられない」（同じ・違うのどちらにも倒さない）。
            print(f"同一性を確かめられません: {exc}")
            return 2
        if result.same:
            return 0
        print(describe(result))
        return 1
    print("使い方: serving_code.py identity <repo_root> | compare <repo_root> <申告>", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
