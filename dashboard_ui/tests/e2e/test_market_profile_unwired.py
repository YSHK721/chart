"""MP 段階 2a（ISSUE-500・依頼者 y 2026-09-06）: サーバ側 MP の**結線解除**を機械的に固定する。

何を固定するか（撤去そのものではなく「計算が起きないこと」）:
    第 1 段階でフロントは MP 列をライブ core の `/market_profile` から借りるようになり、
    `/reach_sheet` の MP 欄は**1 バイトも読まれなくなった**。読まれない量を毎 epoch
    作り続けるのは CLAUDE.md 絶対命令 §4.1 が禁じる「作ってから捨てる」計算であり、
    応答は正しいままなので**状態検証では原理的に落ちない**（ISSUE-450 と同型）。
    段階 2a は最小可逆段階である——Composition Root の注入を外して計算を止めるだけで、
    応答の MP 欄・Port・Gateway・既存検定はそのまま残す（撤去は不可逆なので別ターンの
    y/n＝ISSUE-500 第 2 段階 2b）。

数える面（回数であって時間ではない・§4.1）:
    MP の計算面は 2 つある。畳み込み（`_reference_compute`）と、ビン数を決める価格レンジ面
    （`_core_price_range`）である。片方だけを計装すると、もう一方だけが走り続けても勘定は 0 の
    まま・出力も正しいままで、やはり原理的に落ちない（`tests/complexity/
    test_market_profile_computed_once.py` が同じ理由で 2 面を数えている）。

殻を検査ごとに立てる理由:
    `MaterialStore` は殻の寿命で素材を持ち越す。module 共有の殻を使うと、先行検査が
    プロファイルを温めてしまい「発行 0」が空虚に成立する（既存
    `test_serve_dashboard_smoke.py` が同じ罠を明記している）。

固定するのは**無駄の不在**であって回数ではない。「N 回呼ばれること」は書かない。

ライブデータ依存の表明を置かない（ISSUE-503・実測 2026-09-07）:
    本検定は実データセット `jp225_tick` を実 HTTP 経路で叩く。このデータセットは市場稼働中に
    更新され続けるため、**応答の内容から導かれる量**を表明に使うと、検定の目的とは無関係な
    理由で赤くなる。実際、非空虚性の担保に使っていた「束を増やせばラダーの行数も増える」は、
    2 指標の水準が同一ラダー行へ量子化されない偶然に依存しており、同一 `bar_limits` の
    数分間隔の再実行で行数が 3 → 1 → 2 と動いた（素材量を変えた 6 点のうち 5 点で偽）。

    ここで表明してよいのは「実束縛点＋実データセットで発行が 0 であること」だけである。
    入力量を変えたオーダーの表明のうち**行数を軸にするもの**は合成素材で決定的に測れる
    場所（`tests/complexity/test_market_profile_computed_once.py`）が持つ。
"""
from __future__ import annotations

import contextlib
import json
import threading
import urllib.error
import urllib.request

import pytest

from dashboard_ui.adapter.gateway import market_profile_gateway
from dashboard_ui.adapter.gateway.market_profile_gateway import MarketProfileGateway
from dashboard_ui.adapter.gateway.material_store import MaterialStore
from dashboard_ui.framework.serve_dashboard import make_server
from dashboard_ui.main.composition_root import build_dashboard_app

REF = "jp225_tick"
#: 実素材を読む本数（e2e smoke と同じ絞り方。速度のため）。
BAR_LIMITS = {"1m": 600}
#: 実データで叩く最小の束（e2e smoke と同一）。
INSTANCES = [
    {"instance_id": "ma-24", "indicator_id": "moving_averages", "variant": "default",
     "params": {"source": "hlc3", "ma_type": "ema", "length": 24}},
    {"instance_id": "marod", "indicator_id": "ma_marod", "variant": "default",
     "params": {"source": "hlc3", "ma_type": "ema", "length": 50}},
]
BODY = {"dataset_ref": REF, "chart_timeframe": "1m", "mode": "full",
        "instances": INSTANCES}


class CountingSurface:
    """計算面の Test Spy（本物へ委譲する＝面の挙動は変えず、発行だけ数える）。"""

    def __init__(self, inner) -> None:
        self._inner = inner
        self.calls: "list[tuple]" = []

    def __call__(self, *args, **kwargs):
        self.calls.append((args, tuple(sorted(kwargs))))
        return self._inner(*args, **kwargs)


@pytest.fixture
def mp_surfaces(monkeypatch) -> "tuple[CountingSurface, CountingSurface]":
    """MP の 2 つの計算面に口を結ぶ（畳み込み・価格レンジ）。"""
    folding = CountingSurface(market_profile_gateway._reference_compute)  # noqa: SLF001
    price_range = CountingSurface(market_profile_gateway._core_price_range)  # noqa: SLF001
    monkeypatch.setattr(market_profile_gateway, "_reference_compute", folding)
    monkeypatch.setattr(market_profile_gateway, "_core_price_range", price_range)
    return folding, price_range


@contextlib.contextmanager
def serving():
    """本検査**専用**の殻を 1 つ立てる（素材ストアは冷えた状態で始まる）。"""
    server = make_server(build_dashboard_app(bar_limits=BAR_LIMITS), port=0)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def post(base: str, path: str, payload) -> "tuple[int, dict]":
    request = urllib.request.Request(
        base + path, data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        body = error.read().decode("utf-8")
        return error.code, json.loads(body) if body else {}


# ------------------------------------------------------------------ 結線解除
def test_the_composition_root_does_not_wire_the_market_profile_port() -> None:
    """段階 2a: 束縛点（唯一の結線箇所）が P-MP を注入しない。

    controller の既定に落ちる＝usecase 側（`dashboard_ui/usecase/build_reach_sheet.py`）は
    無改変のまま全行 None を返す。
    """
    # Arrange / Act
    controller = build_dashboard_app(bar_limits=BAR_LIMITS).controller_factory()

    # Assert
    assert controller._mp_port is None  # noqa: SLF001 — 結線の有無は束縛点の観測点


# -------------------------------------------------------- 計算量（ISSUE-500 条件 4）
def test_a_reach_sheet_request_issues_no_market_profile_computation(mp_surfaces) -> None:
    """§4.1: 1 要求あたりの MP 発行は **0**（畳み込み・価格レンジのどちらの面も）。

    オーダーの表明は入力を変えた 2 点で行う: **束の大きさ**（1 / 2 instance）と
    **要求の繰り返し数**（1 / 4 回）。どちらを増やしても発行は 0 のままである。
    固定するのは無駄の不在であって回数ではない——「N 回呼ばれること」を書くと浪費が
    仕様へ昇格する。

    ラダーの**行数**を軸にしたオーダーの表明はここでは行わない（ISSUE-503 で是正）:
        以前は「束を増やせば行数も増える」を非空虚性の担保に使っていた。しかし行数は
        (a) `jp225_tick` が市場稼働中に更新され続けること、(b) 2 つの指標の水準が同じ
        ラダー行へ量子化されないこと、の両方に依存する**ライブデータ依存の量**である。
        実測 2026-09-07（HEAD・同一 `bar_limits={"1m": 600}`・数分間隔）: 第 2 束の
        行数は 3 → 1 → 2 と動き、素材量を変えた 6 点のうち 5 点で「行数が増える」は
        **偽**だった（ma_marod の q95 / q5 の射影行が縮退の記録も無く消える）。
        つまりこの表明は検定の目的（MP 発行が 0 であること）と無関係な理由で赤くなる。

        行数を軸にしたオーダーの表明そのものは正しい不変量であり、**決定的に測れる場所**
        に既に在る: `tests/complexity/test_market_profile_computed_once.py` の
        test_asking_for_more_row_prices_does_not_issue_more_profiles（合成素材で行数を
        3 / 71 に固定して 2 点表明）。e2e が担うのは「実束縛点＋実データセットで 0」で
        あり、行数の制御はここでは原理的にできない。

    非空虚性（0 が「経路が動かなかったから」でないことの担保）:
        1. 応答が `unchanged` の短絡ではなく、実際にシートが組まれていること
        2. 全行が mp 欄を運んでいること＝MP を消費する地点が実行され null を返した
        3. 数えている面が要求経路から到達可能であること
           （下の `test_the_request_path_reaches_the_counted_surfaces_when_the_port_is_wired`
           が結線の有無だけを変えた変異で示す）
    """
    # Arrange
    folding, price_range = mp_surfaces
    smaller = dict(BODY, instances=INSTANCES[:1])

    # Act
    responses = []
    with serving() as url:
        responses.append(post(url, "/reach_sheet", smaller))
        for _ in range(4):
            responses.append(post(url, "/reach_sheet", BODY))

    # Assert
    for status, response in responses:
        assert (status, response["ok"]) == (200, True)
        # 短絡（省リソース段階 2 の unchanged）ではなく、実際にシートが組まれている。
        assert "unchanged" not in response
        assert response["rows"]
        # MP を消費する地点が実行され、密度なしを返した（欄が在って null）。
        assert all("mp" in row and row["mp"] is None for row in response["rows"])
    assert (len(folding.calls), len(price_range.calls)) == (0, 0)


def test_the_request_path_reaches_the_counted_surfaces_when_the_port_is_wired(
    mp_surfaces,
) -> None:
    """検出力（変異検定）: 上の 0 は「面の取り違え」でも「経路が届かない」ためでもない。

    束縛点が組んだ**本物の口**（P-1 / P-2 / 役割宣言 / 前進評価）をそのまま使い、P-MP
    だけを結び直して 1 要求を通す。結線すれば発行は 0 でなくなり、密度が応答へ現れる。

    gateway 単体を直に叩く対照では「Spy が gateway の内部を捕えている」ことしか言えず、
    **要求経路がその面を通る**ことは固定できない（要求経路が P-MP へ届かなくなっても
    上の 0 は成立し、検定は緑のまま浪費の不在を偽証しうる）。だから経路ごと通す。
    """
    # Arrange
    folding, price_range = mp_surfaces
    controller = build_dashboard_app(bar_limits=BAR_LIMITS).controller_factory()
    # 結線の有無だけを変える変異（他の口は束縛点が組んだものを一切差し替えない）。
    controller._mp_port = MarketProfileGateway(          # noqa: SLF001
        bar_port=controller._bar_port, store=MaterialStore(),   # noqa: SLF001
    )

    # Act
    response = controller.handle(BODY)

    # Assert
    assert response["ok"] is True
    assert len(folding.calls) > 0
    assert len(price_range.calls) > 0
    # 発行が出力に届いている（数えた面が本当に MP 欄の唯一源である証拠）。
    assert any(row["mp"] is not None for row in response["rows"])


# ------------------------------------------------------------------ 応答契約
def test_every_row_carries_the_mp_field_and_reports_no_density() -> None:
    """欄は**存在して null**（段階 2a は可逆側だけを実施する）。

    欄そのものを消すのは不可逆な 2b の仕事であり、別ターンの y/n を要する。ここで欄まで
    消すと、フロント（ライブ借用）と Port の契約を先取りで壊すことになる。
    """
    # Arrange / Act
    with serving() as url:
        status, response = post(url, "/reach_sheet", BODY)

    # Assert
    assert (status, response["ok"]) == (200, True)
    assert response["rows"]
    assert all("mp" in row for row in response["rows"])
    assert all(row["mp"] is None for row in response["rows"])
