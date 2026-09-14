"""dashboard core（127.0.0.1:8481）の殻が正しく結線されているかを実 HTTP で確認する。

`indigators/indicator_ui/api/tests/test_server_smoke.py` と同型: エフェメラルポートで
`ThreadingHTTPServer` を立て、`urllib.request` で叩く（fake を挟まない・stdlib のみ）。
純ロジック（連続量 p の算出・ラダー・到達判定）は単体で網羅済みなので、ここで見るのは
**結線**である: Composition Root が束ねた口が実データで応答するか。

unified_ui/serve.sh は `GET /` が 200 を返すまで待ってから router を起動する
（`wait_up`）。したがって `GET /` は web/ が未実装でも 200 でなければならない。
"""
from __future__ import annotations

import contextlib
import json
import threading
import urllib.error
import urllib.request

import pytest

from dashboard_ui.framework.serve_dashboard import make_server
from dashboard_ui.main.composition_root import build_dashboard_app

REF = "jp225_tick"

#: 実データで叩く最小の束（1m の 2 本だけ。速度のために本数を絞る）。
INSTANCES = [
    {"instance_id": "ma-24", "indicator_id": "moving_averages", "variant": "default",
     "params": {"source": "hlc3", "ma_type": "ema", "length": 24}},
    {"instance_id": "marod", "indicator_id": "ma_marod", "variant": "default",
     "params": {"source": "hlc3", "ma_type": "ema", "length": 50}},
]


@contextlib.contextmanager
def serving(**overrides):
    """殻を 1 つ立てて base URL を渡す（**素材ストアはこの殻の寿命**＝冷えた状態で始まる）。

    `overrides` は Composition Root へそのまま渡す（素材の bridge・時計の注入に使う）。
    渡さなければ既定の結線（ライブ core の bridge と壁時計）のままである。
    """
    server = make_server(
        build_dashboard_app(bar_limits={"1m": 600}, **overrides), port=0
    )
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.fixture(scope="module")
def base() -> str:
    """既定の結線（ライブ core の bridge と壁時計）で立てた殻。"""
    with serving() as url:
        yield url


@pytest.fixture(scope="module")
def fixed_base(shared_fixed_bridge, shared_fixed_clock) -> str:
    """**素材と時計を検定が支配する**殻（ISSUE-503 事象 B と同一欠陥クラスの是正）。

    表示量（水準の並び・セルの読み・背景ストリップ）の表明は、素材の品揃えに依存する。
    ライブ素材で測ると、市場の状態しだいで表明が偽になる——実測 2026-09-08: 同一 HEAD で
    前日は 9 区間すべて緑、翌日は 9 区間すべて `p=null` / `tail_unscaled=true` で赤。
    表明の意味は 1 文字も変えず、素材だけを合成へ移す（合成素材では 9/9 で p が立つ）。

    結線そのもの（既定の bridge が実データで応答すること）は `base` 側の
    `test_the_reach_sheet_answers_with_real_material` が引き続き押さえる。
    """
    with serving(
        bridge=shared_fixed_bridge.namespace(), now=shared_fixed_clock
    ) as url:
        yield url


def post(base: str, path: str, payload) -> "tuple[int, dict]":
    data = payload if isinstance(payload, bytes) else json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        base + path, data=data, headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        body = error.read().decode("utf-8")
        return error.code, json.loads(body) if body else {}


def get(base: str, path: str) -> "tuple[int, str, bytes]":
    try:
        with urllib.request.urlopen(base + path, timeout=30) as response:
            return (response.status, response.headers.get("Content-Type", ""),
                    response.read())
    except urllib.error.HTTPError as error:
        return error.code, error.headers.get("Content-Type", ""), error.read()


def test_the_root_answers_so_that_serve_sh_can_wait_for_it(base: str) -> None:
    status, content_type, body = get(base, "/")

    assert status == 200
    assert "text/html" in content_type
    assert body


def test_the_reach_sheet_answers_with_real_material(base: str) -> None:
    status, response = post(base, "/reach_sheet", {
        "dataset_ref": REF, "chart_timeframe": "1m", "mode": "full",
        "instances": INSTANCES,
    })

    assert status == 200
    assert response["ok"] is True
    assert response["current_price"] > 0.0
    assert response["rows"]
    assert response["cells"]


def test_the_rows_are_sorted_by_price_and_split_at_the_current_price(
    fixed_base: str,
) -> None:
    """並び替えはサーバ側で終わっている（フロントは再計算しない）。"""
    _status, response = post(fixed_base, "/reach_sheet", {
        "dataset_ref": REF, "chart_timeframe": "1m", "mode": "full",
        "instances": INSTANCES,
    })

    prices = [row["price"] for row in response["rows"]]
    index = response["current_index"]

    assert prices == sorted(prices, reverse=True)
    assert all(price > response["current_price"] for price in prices[:index])


def test_the_oscillator_cell_comes_back_with_its_reach_state(fixed_base: str) -> None:
    _status, response = post(fixed_base, "/reach_sheet", {
        "dataset_ref": REF, "chart_timeframe": "1m", "mode": "full",
        "instances": INSTANCES,
    })

    cells = [cell for cell in response["cells"] if cell["indicator_id"] == "ma_marod"]

    assert len(cells) == 1
    assert cells[0]["value"] is not None
    assert set(cells[0]["reach"]) == {"reached", "since_time", "truncated"}


def test_the_oscillator_cell_carries_its_trailing_history_strip(fixed_base: str) -> None:
    """§5.2 背景ストリップ（依頼者指示 2026-09-04）: 直近区間の読みが載る。"""
    _status, response = post(fixed_base, "/reach_sheet", {
        "dataset_ref": REF, "chart_timeframe": "1m", "mode": "full",
        "instances": INSTANCES,
    })

    cells = [cell for cell in response["cells"] if cell["indicator_id"] == "ma_marod"]
    history = cells[0]["history"]

    # 10 区間ぶんの履歴を必ず持つ（現在区間 + 過去 9 区間 = 10）。
    assert len(history) == 9
    assert all(
        set(reading) == {"value", "p", "tail_unscaled"} for reading in history
    )
    assert any(reading["p"] is not None for reading in history)
    # 下層（指標ミニ描画）の実値も載る（依頼者明確化 2026-09-04）。
    assert any(reading["value"] is not None for reading in history)


def test_the_shell_computes_through_the_injected_bridge(fixed_bridge, fixed_clock) -> None:
    """R1-1 結線: Composition Root が渡された bridge の**計算面**を口へ通す。

    通していなければ口は既定（ライブ core）へ落ちるので、注入した bridge の計算面は
    1 度も呼ばれない。**呼ばれたこと**が pass-through の唯一の証拠である。

    立証範囲は計算面（full_compute）に限る。時計は
    `test_the_shell_reads_the_injected_clock` が、素材は
    `test_the_fixed_material_answers_identically_twice` が別に押さえる——宣言を立証より
    広く書くと、通っていない口があっても文章だけが「全部通した」と言い続ける
    （是正レビュー Y-5）。
    """
    body = {"dataset_ref": REF, "chart_timeframe": "1m", "mode": "full",
            "instances": INSTANCES}

    with serving(bridge=fixed_bridge.namespace(), now=fixed_clock) as url:
        status, response = post(url, "/reach_sheet", body)

    assert (status, response["ok"]) == (200, True)
    assert fixed_bridge.full_calls != []


def test_the_shell_reads_the_injected_clock(fixed_bridge, fixed_clock) -> None:
    """R-1 結線: Composition Root が渡した時計を、素材の巻き戻しが実際に読む。

    「呼ばれたことが pass-through の唯一の証拠」を bridge と対称に now へも適用する
    （是正レビュー R-1）。是正前は注入口へ値を渡すだけで誰も読んでおらず、Root から
    `now=now` を削っても全件緑のままだった＝結線が無検定だった。
    """
    body = {"dataset_ref": REF, "chart_timeframe": "1m", "mode": "full",
            "instances": INSTANCES}

    with serving(bridge=fixed_bridge.namespace(), now=fixed_clock) as url:
        status, response = post(url, "/reach_sheet", body)

    assert (status, response["ok"]) == (200, True)
    assert fixed_clock.reads > 0


def _repeat(bridge, clock, *, repeats: int) -> "tuple[int, dict, int, int]":
    """殻を 1 つ立てて同じ要求を warm ＋ `repeats` 回投げる。

    返すのは (status, 最後の応答, warm 時点の発行数, warm 後の追加発行数)。**回数そのものは
    返さない**——固定するのは無駄の不在であって、何回呼ぶかという実装詳細ではない。
    """
    body = {"dataset_ref": REF, "chart_timeframe": "1m", "mode": "tick",
            "instances": INSTANCES}
    with serving(bridge=bridge.namespace(), now=clock) as url:
        post(url, "/reach_sheet", body)       # この epoch の素材を作る（初回だけ）
        warmed = len(bridge.full_calls)
        for _ in range(repeats):
            status, response = post(url, "/reach_sheet", body)
    return status, response, warmed, len(bridge.full_calls) - warmed


def test_a_repeated_tick_request_issues_no_additional_material(
    fixed_bridge, fixed_clock
) -> None:
    """結線の検査（ISSUE-457）: **実 HTTP を繰り返し叩いて** P-1 の追加発行が 0 であること。

    素材の共有は Composition Root の結線でしか成立しない（口は要求ごとに組み直されるため、
    gateway 単体をいくら正しくしても、ストアを渡し忘れれば無言で毎要求作り直しに戻る）。
    受け口の単体検査では落ちない欠陥なので、ここは**殻から通す**。殻は本検査専用に立てる
    （module 共有の殻を使うと素材が既に温まっていて、初回発行 0 の空虚な検査になる）。

    数えるのは確定素材の発行（full_compute）だけである。形成中足の末尾 1 点（増分ディス
    パッチ）は段 2 の観測値更新であり、要求ごとに出るのが仕様（§7）。

    素材と時計は**検定が支配する**（ISSUE-503 事象 B）。本番の bridge を使うと観測の途中で
    1m 足が確定して素材の版が進み、仕様どおりの再構築を「増えた」と読んでしまう。
    """
    status, response, warmed, additional = _repeat(fixed_bridge, fixed_clock, repeats=3)

    assert (status, response["ok"]) == (200, True)
    assert warmed > 0                          # 初回は確かに発行している（空虚な検査でない）
    assert response["degradations"] == []      # 縮退で逃げて発行 0 になっていない
    assert additional == 0


def test_the_number_of_repeats_does_not_change_the_material_issuance(
    new_fixed_bridge, fixed_clock
) -> None:
    """オーダーの表明（2 点固定）: 繰り返し 3 / 12 のどちらでも追加発行は 0。

    入力（繰り返し数）を増やしても発行が増えないことを 2 点で押さえる。回数そのものは
    焼き込まない——焼き込むと浪費が仕様へ昇格する（絶対命令 §4.1）。
    """
    additional = {}
    for repeats in (3, 12):
        _status, _response, warmed, extra = _repeat(
            new_fixed_bridge(), fixed_clock, repeats=repeats
        )
        assert warmed > 0
        additional[repeats] = extra

    assert additional[3] == 0
    assert additional[12] == 0


def test_the_fixed_material_answers_identically_twice(fixed_bridge, fixed_clock) -> None:
    """決定性: 素材と時計を固定した殻は、同じ要求へ 2 回とも同じ応答を返す。

    計算量の表明（追加発行 0）は「版が観測の間ずっと不変」を前提にする。その前提が実際に
    成り立っていることを、状態の側から独立に裏取りする。
    """
    body = {"dataset_ref": REF, "chart_timeframe": "1m", "mode": "full",
            "instances": INSTANCES}

    with serving(bridge=fixed_bridge.namespace(), now=fixed_clock) as url:
        _status, first = post(url, "/reach_sheet", body)
        _status, second = post(url, "/reach_sheet", body)

    assert first["rows"] != []
    assert first["cells"] != []
    assert second == first


def test_an_unknown_dataset_is_reported_as_a_failure(base: str) -> None:
    status, response = post(base, "/reach_sheet", {
        "dataset_ref": "nope", "chart_timeframe": "1m", "instances": INSTANCES,
    })

    assert status == 400
    assert response["ok"] is False
    assert response["error"]["message"]


def test_a_broken_body_is_reported_as_a_failure(base: str) -> None:
    status, response = post(base, "/reach_sheet", b"{not json")

    assert status == 400
    assert response["ok"] is False
    assert response["error"]["type"] == "validation"


def test_an_unknown_endpoint_is_not_served(base: str) -> None:
    status, _response = post(base, "/nope", {})

    assert status == 404


def test_a_path_traversal_is_not_served(base: str) -> None:
    """配信面の外へ抜けられない（CWE-22。防御は共有の StaticFileServer が持つ）。"""
    status, _content_type, _body = get(base, "/../../etc/passwd")

    assert status == 404
