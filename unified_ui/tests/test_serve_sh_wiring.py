"""`unified_ui/serve.sh` と `router.py` の結線契約テスト（ISSUE-452 / arch-spec §7）。

なぜソースを読むのか（ISSUE-291 の実測と同型）: 受け口（router のマッピング）を作っても、
起動側が送らなければ**無言で死ぬ**。router の既定表にモードが在るのに serve.sh が
`--upstream` を渡さず core も起動しないと、そのモードは 502／404 になるだけで、起動時には
何のエラーも出ない。よって「既定表の全モードが serve.sh から起動・結線されている」ことを
機械的に固定する（モードを足したときの取り残しを検出する唯一の壁）。

実プロセスは起動しない（core 実体は別工程で作る）。本テストが固定するのは
**router が `/dashboard` を 8481 へプロキシする結線**であり、その実プロキシ挙動そのものは
`test_router.py` の A9 群が実サーバで固定している。

構造は AAA。テスト名は「対象_条件_期待結果」。
"""

from __future__ import annotations

import os
import re

import pytest

import router as router_mod

SERVE_SH_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "serve.sh"
)

with open(SERVE_SH_PATH, encoding="utf-8") as _handle:
    SERVE_SH = _handle.read()

#: 既定表のモード名（router が唯一源。serve.sh 側に第 2 の一覧を持たない）。
DEFAULT_MODES = sorted(router_mod._DEFAULT_UPSTREAMS)


def _port_of(url: str) -> int:
    """`http://127.0.0.1:8481` → 8481。"""
    return int(url.rsplit(":", 1)[1])


@pytest.mark.parametrize("mode", DEFAULT_MODES)
def test_serve_sh_declares_a_port_variable_matching_the_router_default(mode):
    # Arrange
    expected_port = _port_of(router_mod._DEFAULT_UPSTREAMS[mode])
    # Act
    found = re.search(rf"^{mode.upper()}_PORT=(\d+)$", SERVE_SH, re.MULTILINE)
    # Assert: 内部ポートの値が router の既定と食い違うと、serve.sh の起動先と
    #   `--upstream` 無指定時の既定が別のポートを指す（片方だけ直して片方を忘れる形の事故）。
    assert found is not None, f"{mode.upper()}_PORT が serve.sh に無い"
    assert int(found.group(1)) == expected_port


@pytest.mark.parametrize("mode", DEFAULT_MODES)
def test_serve_sh_passes_every_default_mode_as_an_upstream_argument(mode):
    # Arrange
    expected = f'--upstream "{mode}=http://127.0.0.1:${{{mode.upper()}_PORT}}"'
    # Act / Assert: 渡し忘れたモードは router の既定へ落ちるか、そもそも振り分けられない。
    assert expected in SERVE_SH, f"serve.sh が {mode} の --upstream を渡していない"


@pytest.mark.parametrize("mode", DEFAULT_MODES)
def test_serve_sh_waits_for_every_default_mode_core_to_come_up(mode):
    # Arrange
    expected = f'wait_up "http://127.0.0.1:${{{mode.upper()}_PORT}}/"'
    # Act / Assert: 起動待ちが無いモードは、core が立ち上がる前にルータが公開され、
    #   最初の要求だけが 502 になる（再現しにくい起動時レースになる）。
    assert expected in SERVE_SH, f"serve.sh が {mode} core の起動を待っていない"


def test_serve_sh_starts_the_dashboard_core_with_the_venv_python():
    # Arrange / Act / Assert: dashboard core は単独起動 serve.sh を持たない（sim と同じ裁定）。
    #   venv python へ Composition Root を直接与える形（sim core の start_sim_core と同形）。
    assert "start_dashboard_core()" in SERVE_SH
    assert 'DASHBOARD_PGID="$(start_dashboard_core)"' in SERVE_SH
    assert "dashboard_ui.framework.serve_dashboard" in SERVE_SH
    # venv python で起動する（生 python では import パスも依存も解決できない）。
    start = SERVE_SH.split("start_dashboard_core()", 1)[1].split("\n}", 1)[0]
    assert '"$VENV_PY"' in start
    assert "${DASHBOARD_PORT}" in start


def test_serve_sh_stops_the_dashboard_core_on_exit():
    # Arrange / Act / Assert: 停止側に足し忘れると、Ctrl-C 後も 8481 が握られたまま残り、
    #   次回起動の bind が失敗する（sim core が ISSUE-348 で通った経路と同型）。
    assert 'DASHBOARD_PGID=""' in SERVE_SH
    cleanup = SERVE_SH.split("cleanup() {", 1)[1].split("\n}", 1)[0]
    assert '-"$DASHBOARD_PGID"' in cleanup


def test_serve_sh_announces_the_dashboard_route_to_the_operator():
    # Arrange / Act / Assert: 起動時に出す経路一覧へ第 4 モードが載っている
    #   （どのモードがどの core へ行くかは検証の前提・ISSUE-348）。
    assert 'echo "  /dashboard/*    → 127.0.0.1:${DASHBOARD_PORT}"' in SERVE_SH
