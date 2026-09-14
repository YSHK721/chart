"""静的配信の許可根が **仕事の量を増やさない** ことを回数で固定する（ISSUE-502 C-4 3b）。

## なぜ状態検証では足りないか

``StaticFileServer.resolve`` は「どの許可根の配下か」を判定するだけなので、許可根を 1 つ増やす
実装が根ごとに ``resolve()`` / ``is_file()`` を発行しても**出力は正しいまま**である。共有フロント
供給パッケージは台帳（common.shared_web_roots）へ足すだけで増えるので、根の数に比例して
ファイルシステム発行が増える実装だと、台帳が伸びるたびに全リクエストが静かに重くなる
（ISSUE-450 と同型：出力が正しいので既存の配信検定では原理的に落ちない）。

## 何を固定するか（回数であって時間ではない）

1. **無駄の不在**: 1 回の解決で発行した fs 探索 − 出力に使った実パス = 0。
2. **オーダーの表明**: 台帳を 1 件から 5 件へ増やしても発行が増えない（許可根の判定は
   ``is_relative_to`` の純粋な文字列比較であり、根を触らない）。
3. **検出力**: 根ごとに探索する変異を実際に入れると 2 が赤になる（検定が空振りしていない）。

回数そのもの（「N 回呼ばれること」）は期待値に焼き込まない。固定するのは差が 0 であること
と、入力を増やしても差が 0 のままであることである。
"""
from __future__ import annotations

from pathlib import Path

import pytest

from simulator.replay_ui.framework import static_file_server as sfs_mod
from simulator.replay_ui.framework.static_file_server import StaticFileServer


class _FsProbeSpy:
    """``Path`` のファイルシステム探索（resolve / is_file）の発行を数える Test Spy。"""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.resolves: "list[str]" = []
        self.is_files: "list[str]" = []
        original_resolve = Path.resolve
        original_is_file = Path.is_file

        def counting_resolve(path_self, *args, **kwargs):
            self.resolves.append(str(path_self))
            return original_resolve(path_self, *args, **kwargs)

        def counting_is_file(path_self, *args, **kwargs):
            self.is_files.append(str(path_self))
            return original_is_file(path_self, *args, **kwargs)

        monkeypatch.setattr(Path, "resolve", counting_resolve)
        monkeypatch.setattr(Path, "is_file", counting_is_file)

    @property
    def issued(self) -> int:
        return len(self.resolves) + len(self.is_files)


@pytest.fixture
def tree(tmp_path: Path):
    """``<indigators>/indicator_ui/web`` と consumer の web 根を実ファイルで組む。

    台帳が名指す供給パッケージも同じ ``indigators`` 根の下に作るので、``_allowed_roots`` は
    実在する根を返す（存在しない根で通ってしまう検定にしない）。
    """
    indigators = tmp_path / "indigators"
    shared = indigators / "indicator_ui" / "web"
    for sub in ("js", "css", "vendor"):
        (shared / sub).mkdir(parents=True)
    web = tmp_path / "consumer_web"
    (web / "js").mkdir(parents=True)
    (web / "js" / "own.js").write_text("OWN", encoding="utf-8")
    for name in sfs_mod.shared_web_js_roots(indigators):
        name.mkdir(parents=True, exist_ok=True)
    return StaticFileServer(web.resolve(), shared.resolve()), indigators


def _with_ledger(monkeypatch: pytest.MonkeyPatch, indigators: Path, count: int) -> None:
    """台帳を ``count`` 件へ差し替え、その供給根を実在させる（根の数だけを振る）。"""
    names = tuple(f"pkg{i}" for i in range(count))
    monkeypatch.setattr(sfs_mod, "shared_web_js_roots",
                        lambda root, _names=names: tuple(
                            root / n / "web" / "js" for n in _names))
    for name in names:
        (indigators / name / "web" / "js").mkdir(parents=True, exist_ok=True)


def test_no_wasted_probe_on_a_hit(tree, monkeypatch: pytest.MonkeyPatch) -> None:
    """発行した fs 探索 − 出力に使った実パス = 0（作ってから捨てる探索が無い）。"""
    server, _indigators = tree
    spy = _FsProbeSpy(monkeypatch)
    found = server.resolve("/js/own.js")
    assert found is not None
    # 出力に使ったのは 1 本の実パス。resolve と is_file をそれぞれ 1 回ずつ発行した以上は無駄。
    assert spy.issued - 2 * 1 == 0, (
        f"捨てられる探索が混ざっている: resolve={spy.resolves} is_file={spy.is_files}"
    )


@pytest.mark.parametrize("ledger_size", [1, 5])
def test_probe_count_does_not_grow_with_the_ledger(
    tree, monkeypatch: pytest.MonkeyPatch, ledger_size: int
) -> None:
    """台帳（許可根）を増やしても fs 発行は増えない（オーダーの表明・2 点で固定）。"""
    server, indigators = tree
    _with_ledger(monkeypatch, indigators, ledger_size)
    spy = _FsProbeSpy(monkeypatch)
    assert server.resolve("/js/own.js") is not None
    assert spy.issued - 2 == 0, (
        f"許可根 {ledger_size} 件で発行が増えている（根ごとに探索している疑い）: "
        f"resolve={spy.resolves} is_file={spy.is_files}"
    )


def test_gate_detects_a_per_root_probing_mutation(
    tree, monkeypatch: pytest.MonkeyPatch
) -> None:
    """検出力: 許可根ごとに探索する変異を入れると上の検定が赤になる（空振りでない）。"""
    server, indigators = tree
    _with_ledger(monkeypatch, indigators, 5)
    spy = _FsProbeSpy(monkeypatch)

    original_allowed = server._allowed_roots

    def probing_allowed_roots():
        roots = original_allowed()
        # 変異: 判定の前に根ごとの実在確認を撒く（出力は 1 バイトも変わらない）。
        return tuple(r for r in roots if r is None or r.resolve())

    monkeypatch.setattr(server, "_allowed_roots", probing_allowed_roots)
    assert server.resolve("/js/own.js") is not None
    assert spy.issued - 2 != 0, "根ごとの探索を入れても発行が増えない＝検定が空振り"
