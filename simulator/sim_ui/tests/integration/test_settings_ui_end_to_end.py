"""Tester Settings の実 UI 経路の端から端まで（Phase 8 スライス 6・NFR-09）。

固定する不変条件（設計 §18.4 スライス 6 の通過条件）:
    1. `GET /sim/settings-schema` が実 HTTP で 200 を返し、選択肢が**列挙由来**である
       （front が候補を内蔵していないことは web 側の構造ガードが、配信まで届くことは本件が固定する）。
    2. Tester フォーム込みの投入が受理され、ジョブが完了し、成果物（stats.json / report.json）が
       結果配信面から取得できる。
    3. 同一指定 2 回で `stats.json` が **byte 完全一致**する（決定性）。
    4. settings 不在の旧本文の投入は従来どおり完了する（併存・現行経路に 1 bit も漏れない）。
    5. `Period` を実行対象データセットと食い違わせた投入は**失敗し、理由が応答に載る**
       （沈黙で別の時間足の結果を出さない）。front 側は投入前に警告を出している。

方式（Phase 7 の先例 ISSUE-382-9 と同じ）: **製品 UI の合成経路**を ephemeral port で立てる。
    - sim core: `build_sim_display_app`（本番の合成根そのもの）
    - 前段: `unified_ui/router.py` の `create_router_server`（`/sim` prefix を剥がす本番の口）
稼働中の公開 8000 スタックには触れない（他者が使用中のため停止・再起動しない）。サーバは
フォアグラウンドのスレッドで持ち、後片付けを fixture が必ず行う。

**投入本文は front 本体が作る**（`web/tests/e2e_submit_driver.mjs` が本番の合成根
`mountSimExecutionPanel` を fake DOM の上で動かす）。python 側で本文を組み直すと front の
組み立て規則の第 2 実装になり、front が実際に作る本文がサーバに通るかを何も確かめられない
（実測: python の素朴な写しは `Leverage='10.0'` を作り規則 J で弾かれた。front は `'10'`）。
"""
from __future__ import annotations

import json
import shutil
import subprocess
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from simulator.sim_ui.framework.serve_sim_display import make_server
from simulator.sim_ui.main.composition_root_display import build_sim_display_app
from unified_ui.router import create_router_server

_ROOT = Path(__file__).resolve().parents[4]
_SIM_WEB = _ROOT / "simulator" / "sim_ui" / "web"
_UNIFIED_WEB = _ROOT / "unified_ui" / "web"
_DRIVER = _SIM_WEB / "tests" / "e2e_submit_driver.mjs"
_NODE = shutil.which("node")

#: front（本番コード）を実際に動かすため node が要る。web スイートと同じ前提。
pytestmark = pytest.mark.skipif(_NODE is None, reason="front を実行するには node が必要です")

#: 実行完了を待つ上限（実測: 本データセット 1 か月の M1 で約 7 秒）。
_RUN_TIMEOUT_S = 300.0
_TERMINAL = {"completed", "failed", "cancelled"}


def _serve_forever(server):
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return thread


@pytest.fixture
def stack(tmp_path: Path):
    """製品 UI の合成経路（ルータ → sim core）を ephemeral port で立てる。"""
    core = build_sim_display_app(repo_root=_ROOT, web_dir=_SIM_WEB, data_root=tmp_path / "data")
    core_server = make_server(core, "127.0.0.1", None)
    core_base = f"http://127.0.0.1:{core_server.server_address[1]}"
    core_thread = _serve_forever(core_server)

    router = create_router_server(
        ("127.0.0.1", 0), upstreams={"sim": core_base}, web_root=str(_UNIFIED_WEB)
    )
    router_thread = _serve_forever(router)
    base = f"http://127.0.0.1:{router.server_address[1]}"
    try:
        yield base
    finally:
        for server, thread in ((router, router_thread), (core_server, core_thread)):
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)


def _request(base: str, path: str):
    req = urllib.request.Request(base + path, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=30) as res:
            return res.status, res.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


def _json(base: str, path: str):
    status, raw = _request(base, path)
    return status, (json.loads(raw) if raw else None)


def _drive(base: str, scenario: str) -> dict:
    """front（本番の合成根）を fake DOM で動かして投入し、その観測を返す。"""
    proc = subprocess.run(
        [_NODE, str(_DRIVER), base, scenario],
        capture_output=True, text=True, timeout=180, cwd=str(_SIM_WEB),
    )
    assert proc.returncode == 0, f"front ドライバが落ちました:\n{proc.stderr}"
    return json.loads(proc.stdout.strip().splitlines()[-1])


def _wait_terminal(base: str, job_id: str) -> dict:
    deadline = time.monotonic() + _RUN_TIMEOUT_S
    payload = None
    while time.monotonic() < deadline:
        status, payload = _json(base, f"/sim/jobs/{job_id}")
        assert status == 200, payload
        if payload["status"] in _TERMINAL:
            return payload
        time.sleep(0.5)
    raise AssertionError(f"ジョブが終端に達しません: {payload}")


# --- 1. schema 面（列挙由来の選択肢が実 HTTP で届く）---------------------------

def test_settings_schema面が実HTTPで200を返し選択肢が列挙由来である(stack) -> None:
    from simulator.main import known_ea_names
    from simulator.main.tester_settings.ea_input_map import SUBJECT_SUFFIX
    from simulator.usecase.tester_settings.enums import TIMEFRAME_INI_LABELS, TickModel

    # Act
    status, payload = _json(stack, "/sim/settings-schema")
    # Assert
    assert status == 200
    assert payload["ok"] is True
    assert {o["token"] for o in payload["enum_options"]["Period"]} == set(TIMEFRAME_INI_LABELS.values())
    assert {o["token"] for o in payload["enum_options"]["Model"]} == {str(int(m)) for m in TickModel}
    tokens = [o["token"] for o in payload["expert_options"]]
    assert {t.removesuffix(SUBJECT_SUFFIX) for t in tokens} == set(known_ea_names())


# --- 2. Tester フォーム込みの投入が完了し成果物が出る -------------------------

def test_Testerフォーム込みの投入が完了し成果物が生成される(stack) -> None:
    # Act
    observed = _drive(stack, "settings")
    # Assert: front は schema を取りに行き、settings ブロックを本文へ載せている
    assert "/sim/settings-schema" in observed["requested_paths"]
    body = observed["body"]
    assert body is not None, observed
    assert body["settings"]["tester"], observed
    assert all(isinstance(v, str) for v in body["settings"]["tester"].values())
    assert body["settings"]["inputs"] == []
    # T-4: 同一概念の入力欄は 1 つ（重複欄は器から消える）
    assert observed["tester_panel_present"] is True
    assert observed["legacy_ea_field_present"] is False
    assert observed["legacy_deposit_field_present"] is False
    # 受理 → 完了 → 成果物
    job_id = observed["submitted"]["job_id"]
    final = _wait_terminal(stack, job_id)
    assert final["status"] == "completed", final
    for name in ("stats.json", "report.json"):
        status, raw = _request(stack, f"/sim/data/{job_id}/{name}")
        assert status == 200, f"{name} が結果配信面から取れません"
        assert raw, name


# --- 3. 決定性（同一指定 2 回で stats.json が byte 一致）-----------------------

def test_同一指定2回のstats_jsonがbyte完全一致する(stack) -> None:
    # Arrange / Act
    payloads = []
    for _ in range(2):
        observed = _drive(stack, "settings")
        job_id = observed["submitted"]["job_id"]
        assert _wait_terminal(stack, job_id)["status"] == "completed"
        status, raw = _request(stack, f"/sim/data/{job_id}/stats.json")
        assert status == 200
        payloads.append(raw)
    # Assert
    assert payloads[0] == payloads[1], "同一指定の 2 回で stats.json が一致しません（非決定）"


# --- 4. settings 不在の旧本文が従来どおり通る（併存）--------------------------

def test_settings不在の旧本文の投入も従来どおり完了する(stack) -> None:
    # Act
    observed = _drive(stack, "legacy")
    # Assert: schema を取れない構成では settings を載せず、旧フォームの欄が権威のまま
    assert "settings" not in observed["body"], observed["body"]
    assert observed["tester_panel_present"] is True, "fail-open のはずがパネルごと消えています"
    assert observed["legacy_ea_field_present"] is True
    # 初期資金はドライバが入力した値（既定値では本データセットがストップアウトに達し、
    # **旧経路のエンジン既定 `fail_stop` は完走しない**＝Phase 8 以前からの既存挙動）。
    assert observed["body"]["backtest"]["initial_deposit"] > 0
    job_id = observed["submitted"]["job_id"]
    assert _wait_terminal(stack, job_id)["status"] == "completed"
    assert _request(stack, f"/sim/data/{job_id}/stats.json")[0] == 200


# --- 5. データセットと食い違う Period は失敗し理由が残る ----------------------

def test_正しく指定したカスタム期間に偽の非対象告知を出さない(stack) -> None:
    """N-15 は**実行後にしか判定できない**（`detect=None`）。窓を要求しただけで
    「要求した期間窓がエンジンへ適用されていません」と断定表示するのは偽の告知である。

    本検定はその偽陽性を禁じる: データセットの実在範囲を指定した run は**完走する**
    （＝窓は実際に適用されている）のに、UI がその run に N-15 を出していないこと。
    警告が常時点灯すると、本当に非対象な選択の警告まで無視されるようになる。
    """
    # Act
    observed = _drive(stack, "custom_range")
    # Assert: カスタム期間が実際に投入本文へ載っている（何も指定していない run ではない）
    tester = observed["body"]["settings"]["tester"]
    assert "FromDate" in tester and "ToDate" in tester, tester
    assert "Dates" not in tester, "規則 E: プリセットと同時に送っている"
    # 偽の断定を出していない
    assert "N-15" not in observed["active_unsupported"], observed["active_unsupported"]
    # 窓は実際に適用され、run は完走する（＝上の告知が出ていたら偽陽性だったことの実証）
    job_id = observed["submitted"]["job_id"]
    assert _wait_terminal(stack, job_id)["status"] == "completed"


def test_非対象トークンの選択は投入前にUIが理由を示し実行は失敗する(stack) -> None:
    """R-9: 実行段の Fail-Stop に至る**前**に、UI が当該告知の reason を出していること。

    宣言（`UnsupportedRule.ui`）と判定式（`detect`）がずれていると、画面は黙ったまま
    実行だけが落ちる（遅い失敗）。宣言駆動の該当判定が実物の schema で効くことを、
    実 HTTP・実エンジンで固定する。
    """
    from simulator.main.tester_settings.unsupported import RULES

    # Arrange / Act
    observed = _drive(stack, "unsupported")
    # Assert: 投入前に該当が出ている（front が黙っていない）
    assert "N-05" in observed["active_unsupported"], observed["active_unsupported"]
    assert RULES["N-05"].reason in observed["shown_unsupported_reasons"], observed
    # 実行は Fail-Stop（沈黙で別の実行モードにフォールバックしない）
    job_id = observed["submitted"]["job_id"]
    final = _wait_terminal(stack, job_id)
    assert final["status"] == "failed", final
    assert "N-05" in (final["failure_reason"] or ""), final["failure_reason"]


def test_Periodがデータセットと不一致なら失敗し理由が応答に載る(stack) -> None:
    # Act
    observed = _drive(stack, "mismatch")
    # Assert: front は投入前に警告している（沈黙で投入させない）
    assert observed["warnings"], "不一致なのに front が警告していません"
    chosen = observed["body"]["settings"]["tester"]["Period"]
    assert any(chosen in w for w in observed["warnings"])
    # サーバ側は Fail-Stop（別の時間足の結果を沈黙で出さない）
    job_id = observed["submitted"]["job_id"]
    final = _wait_terminal(stack, job_id)
    assert final["status"] == "failed", final
    reason = final["failure_reason"] or ""
    assert "period" in reason, reason
    assert chosen in reason, reason
