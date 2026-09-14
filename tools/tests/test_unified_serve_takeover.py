"""``unified_ui/serve.sh`` の起動 CLI と占有引き継ぎの不変条件を固定する（ISSUE-366 派生）。

なぜ必要か:
    8000 は固定の単一資源なので、別ツリーで検証するには占有の移譲が要る。移譲の**判断**
    （他セッションの作業を落としてよいか）は人のものだが、**手順**（どの PID をどの順で
    止めるか）は機械のものである。旧実装は両方を人へ投げ返しており、起動のたびに
    ``ps`` と ``kill`` を打つ運用になっていた（2026-08-10 に依頼者から手間だと報告）。

本テストが固定する不変条件:
    1. 既定（フラグ無し）は**他ツリーのスタックを落とさない**。判断を勝手に代行しない。
    2. ``--takeover`` が存在し、``--help`` から発見できる（隠し機能にしない）。
    3. 未知の引数は黙って無視せずエラー終了する（``--takover`` のような打ち間違いが、
       「フラグを付けたのに引き継がれない」という分かりにくい失敗にならないこと）。
    4. シグナル送信先の PID 集合から、**自分の側**のプロセス（自分・祖先・同一プロセス
       グループの部分シェル）を必ず除外する。argv の部分一致はそれらまで拾う（実測）。
       除外を外すと、起動元のシェルや自分の部分シェルへ INT を送りうる。

    1〜3 は実際に ``serve.sh`` を起動して確かめる（ポートは掴まない経路のみ）。
    4 は ``pids_with`` を抜き出して実プロセスに対して評価する。
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
_SERVE = _ROOT / "unified_ui" / "serve.sh"


def _run(*args: str, timeout: int = 30) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(_SERVE), *args],
        capture_output=True,
        text=True,
        timeout=timeout,
        cwd=str(_ROOT),
    )


def test_構文が壊れていない() -> None:
    """起動スクリプトは、走らせる前に構文で落ちてはならない。"""
    proc = subprocess.run(
        ["bash", "-n", str(_SERVE)], capture_output=True, text=True, timeout=30
    )
    assert proc.returncode == 0, f"bash -n が失敗した: {proc.stderr}"


def test_helpにtakeoverが載っている() -> None:
    """引き継ぎ手段は --help から発見できること（知っている人だけの隠し機能にしない）。"""
    proc = _run("--help")
    assert proc.returncode == 0, f"--help が異常終了した: {proc.stderr}"
    assert "--takeover" in proc.stdout, f"--help に --takeover が無い:\n{proc.stdout}"


def test_未知の引数はエラー終了する() -> None:
    """打ち間違いを黙って無視しない（無視すると「付けたのに効かない」失敗になる）。"""
    proc = _run("--takover")  # 実際に起こりうる打ち間違い
    assert proc.returncode != 0, "未知の引数を受理してはならない"
    assert "不明な引数" in proc.stderr, f"理由が示されていない: {proc.stderr}"


def test_既定は他ツリーのスタックを落とさない() -> None:
    """フラグ無しで別ツリーが 8000 を握っているときは、停止せずエラー終了すること。

    8000 が空いている環境ではこの分岐に入らないため skip する（起動してしまうと
    テストがポートを掴むので、ここでは決して起動させない）。
    """
    import urllib.error
    import urllib.request

    try:
        with urllib.request.urlopen("http://127.0.0.1:8000/__serving_root", timeout=3) as r:
            serving_root = r.read().decode().strip()
    except (urllib.error.URLError, OSError):
        pytest.skip("8000 が未使用のため、占有分岐を評価できない")

    if serving_root == str(_ROOT):
        pytest.skip("8000 を配信しているのが自ツリーのため、占有分岐を評価できない")

    proc = _run()
    assert proc.returncode != 0, "別ツリー占有時にフラグ無しで起動してはならない"
    assert "--takeover" in proc.stderr, f"引き継ぎ手段を案内していない: {proc.stderr}"

    # 実際に落としていないこと（判断を代行していないことの実証）。
    with urllib.request.urlopen("http://127.0.0.1:8000/__serving_root", timeout=3) as r:
        assert r.read().decode().strip() == serving_root, "占有側を停止してしまった"


def test_pids_withは自分の側のプロセスを除外する(tmp_path: Path) -> None:
    """argv 部分一致で自分の側を拾わないこと（拾うと親シェル・部分シェルへ INT を送る）。

    呼び出し側の argv に一致文字列を含ませることで、祖先シェルと、パイプラインのために
    bash が fork する部分シェルの両方が一致する状況を作る（どちらも実測で一致した）。
    """
    source = _SERVE.read_text(encoding="utf-8")
    funcs = []
    for name in ("ancestor_pids", "pids_with"):
        start = source.index(f"{name}() {{")
        end = source.index("\n}\n", start) + len("\n}\n")
        funcs.append(source[start:end])
    # 呼び出し側の argv に一致文字列を必ず含ませる（旧実装はここで自分自身を拾っていた）。
    needle = "unified-serve-takeover-selftest-needle"
    script = tmp_path / "probe.sh"
    script.write_text("\n".join([*funcs, f'pids_with "{needle}"', ""]), encoding="utf-8")

    proc = subprocess.run(
        ["bash", "-c", f'bash {script} "{needle}"'],
        capture_output=True,
        text=True,
        timeout=30,
        env={**os.environ},
    )
    assert proc.returncode == 0, f"probe が失敗した: {proc.stderr}"
    assert proc.stdout.strip() == "", (
        "自分自身・祖先シェル（argv に一致文字列を持つ）を PID 集合へ入れてはならない。"
        f" 実際に返した PID: {proc.stdout.strip()!r}"
    )


if __name__ == "__main__":  # pragma: no cover
    sys.exit(pytest.main([__file__, "-v"]))
