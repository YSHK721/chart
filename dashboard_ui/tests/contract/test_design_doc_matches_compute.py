"""§7.1 契約テスト — 本書と実装の食い違いを赤で落とす。

期待値は **.doc/PRICE_LEVEL_REACH_SHEET_BASIC_DESIGN.md §7.1.1 の機械可読ブロックだけ**
から来る（読むのは design_doc_contract の 1 か所）。実装からは 1 つも作らない。監査
（docs/tdd-divergence-audit-2026-08-09.md）が挙げた「赤くならないテスト」の 3 つの形を
いずれも踏まない:

===============================  ==========================================
失敗形                            本検査での回避
===============================  ==========================================
実装のソースを grep して          grep しない。**実 `/compute`** の応答
「実装がある」と主張する           （`series[].name`）と突き合わせる
期待値を被検査コードから生成する   期待値は本書から読む（トートロジー回避）
本番が通らない経路を fake で叩く   本番と同じ `framework.server` を
                                  エフェメラルポートで起動して POST する
===============================  ==========================================

リクエストの `params` について:
    系列名は設定に依存する（`q_low=0.05` なら `ma_marod_q5`）。§7.1.1 が
    「展開元はユーザー設定であり被検査コードではない」と定めるとおり、本検査は params を
    **本ファイルに直書き**する。カタログ既定（`call_binding._TABLE`）から引くと展開元が
    被検査コードになり、既定値を変えたときに系列名と期待値が一緒に動いてしまう。

素材について:
    `datasetRef="sample"` を使う。系列**名**の集合は素材に依らないことを実測で確認した
    （2026-08-29・"sample" 2,459 本と `jp225_tick` 5m 49,069 本で全 7 指標の名前集合が一致）。
    §7.1「対象外」のとおり、固定するのは列挙だけで実測値は固定しない。
"""
from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

from dashboard_ui.tests.contract import design_doc_contract as doc


def open_live_core() -> None:
    """ライブ core を in-process で読めるようにする（import 境界はプロダクト側が所有）。

    ライブ core の import パス準備は
    `simulator.replay_ui.adapter._indicator_ui_bridge` が唯一の所有者であり、
    dashboard core（`dashboard_ui.adapter.gateway.indicator_ui_compute_gateway`）も
    replay / sim も同じ入口を通る（arch-spec §3）。テスト側で sys.path を触ると
    「テストが読むモジュール」と「プロダクトが読むモジュール」の同一性が食い違いうるので、
    第 2 の準備を書かない。
    """
    from simulator.replay_ui.adapter import _indicator_ui_bridge

    _indicator_ui_bridge.load_compute()

#: 素材。名前の集合は素材に依らない（module docstring の実測）。
DATASET_REF = "sample"

#: リクエストの params（＝ユーザー設定側。被検査コードから引かない）。
#:
#: 既定と違えている設定と、その根拠:
#:   btlm_trail の "q_out" … 既定は None で、そのとき
#:       `indigators/btlm_trail/src/trail.py:201-210` は上下の外れ値水準を None の
#:       ままにする＝`btlm_trail_off_hi` / `btlm_trail_off_lo` を出さない。本書 §3.1 が
#:       この 2 本を水準として挙げているのは外れ値分位を有効にした設定（`q_out > q_high`）
#:       である。実測に使った設定も同じで、`tools/measure/issue449/probe_levels.py:37-41` の
#:       表示名表が `btlm_trail_off_hi` / `btlm_trail_off_lo` を持っている。
REQUEST_PARAMS: "dict[str, dict[str, object]]" = {
    "moving_averages": {
        "ma_type": "ema", "length": 9, "source": "close", "offset": 0,
        "smoothing_type": "none", "smoothing_length": 9, "bb_stddev": 2.0,
    },
    "btlm_trail": {
        "source": "close", "maxbars": 100, "q_low": 0.05, "q_high": 0.95,
        "band_method": "ols", "empirical_n": 500, "q_out": 0.99,
        "show_metrics": True, "n_cov": 250,
    },
    "cvfe": {
        "n_har": 500, "sigma_inner": 1.0, "sigma_outer": 2.0,
        "show_outliers": True, "display_mode": "dashes", "dash_opacity": 0.5,
    },
    "ma_marod": {
        "source": "close", "ma_type": "ema", "length": 50, "q_low": 0.05,
        "q_high": 0.95, "q_out": 0.99, "k_events": 50, "event_agg": "episode",
        "window_n": 500,
    },
    "btlm_trail_marod": {
        "source": "close", "maxbars": 100, "q_low": 0.05, "q_high": 0.95,
        "q_out": 0.99, "k_events": 50, "event_agg": "episode", "window_n": 500,
    },
    "profit_rsi": {
        "rsi_period": 6, "apply": 5, "window_n": 500, "q_low": 0.10,
        "q_high": 0.90, "q_out": 0.99, "k_events": 50,
    },
    "tickvol": {
        "window_n": 500, "q_low": 0.10, "q_high": 0.90, "q_out": 0.99,
        "k_events": 50,
    },
}


# --------------------------------------------------------------------------- #
# 実 /compute（fake を挟まない）
# --------------------------------------------------------------------------- #
@pytest.fixture(scope="module")
def compute_base() -> str:
    """本番の HTTP 殻をエフェメラルポートで起動する（`test_server_smoke.py` と同型）。"""
    open_live_core()
    from framework.server import IndicatorUIRequestHandler

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), IndicatorUIRequestHandler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{httpd.server_address[1]}"
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)


def response_series_names(base: str, indicator_id: str) -> "frozenset[str]":
    """実 `/compute` が返す `series[].name` の集合。"""
    body = {
        "indicatorId": indicator_id,
        "variant": "default",
        "params": REQUEST_PARAMS[indicator_id],
        "datasetRef": DATASET_REF,
    }
    request = urllib.request.Request(
        base + "/compute",
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=600) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise AssertionError(
            f"{indicator_id}: /compute が {exc.code} を返しました: "
            f"{exc.read().decode('utf-8')[:400]}"
        ) from exc
    assert payload["ok"] is True, (indicator_id, payload)
    return frozenset(series["name"] for series in payload["series"])


# --------------------------------------------------------------------------- #
# 1. 系列名の集合（多くても少なくても赤）
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("indicator_id", sorted(doc.price_scale_ids()))
def test_the_price_scale_indicator_emits_exactly_the_series_the_document_lists(
    indicator_id: str, compute_base: str
) -> None:
    """§3.1 / §7.1.1 の列挙 = 実 `/compute` の `series[].name`（第 1 表の対象）。"""
    # Arrange
    expected = doc.contract_series_names(indicator_id, REQUEST_PARAMS[indicator_id])

    # Act
    actual = response_series_names(compute_base, indicator_id)

    # Assert
    assert actual == expected, (
        f"{indicator_id}: 本書に在って出力されない {sorted(expected - actual)} / "
        f"出力されて本書に無い {sorted(actual - expected)}"
    )


@pytest.mark.parametrize("indicator_id", sorted(doc.oscillator_ids()))
def test_the_oscillator_emits_exactly_the_series_the_document_lists(
    indicator_id: str, compute_base: str
) -> None:
    """§3.2 / §7.1.1 の列挙 = 実 `/compute` の `series[].name`（第 2 表の対象）。"""
    # Arrange
    expected = doc.contract_series_names(indicator_id, REQUEST_PARAMS[indicator_id])

    # Act
    actual = response_series_names(compute_base, indicator_id)

    # Assert
    assert actual == expected, (
        f"{indicator_id}: 本書に在って出力されない {sorted(expected - actual)} / "
        f"出力されて本書に無い {sorted(actual - expected)}"
    )


# --------------------------------------------------------------------------- #
# 2. 足内更新の可否（増分器 factory への登録有無）
# --------------------------------------------------------------------------- #
def contract_indicator_ids() -> "frozenset[str]":
    """契約ブロックが扱う指標のすべて（第 1 表 ＋ 第 2 表）。"""
    return doc.price_scale_ids() | doc.oscillator_ids()


def incrementer_names() -> "frozenset[str]":
    """増分器 factory に登録されている名前（実装側の真・§7.1「何を固定するか」）。"""
    open_live_core()
    from adapter.compute.incremental import _FACTORIES

    return frozenset(_FACTORIES)


def test_the_document_intrabar_update_matches_the_incrementer_registry() -> None:
    """§3.1「足内更新」列 / §7 = `incremental._FACTORIES` への登録有無。"""
    # Arrange
    targets = contract_indicator_ids()
    registered = incrementer_names()

    # Act
    capable = targets & registered

    # Assert
    assert doc.declared_set("intrabar_update", "yes") == capable
    assert doc.declared_set("intrabar_update", "no") == targets - capable


def declared_archetype(indicator_id: str) -> "str | None":
    """`call_binding` の latest_meta が宣言する archetype（未宣言は None）。"""
    open_live_core()
    from adapter.compute.call_binding import latest_meta_fields

    meta = latest_meta_fields(indicator_id, "default", dict(REQUEST_PARAMS[indicator_id]))
    return None if meta is None else meta.archetype


def declared_incrementer(indicator_id: str) -> "str | None":
    """`call_binding` の latest_meta が指名する増分器名（未宣言は None）。"""
    open_live_core()
    from adapter.compute.call_binding import latest_meta_fields

    meta = latest_meta_fields(indicator_id, "default", dict(REQUEST_PARAMS[indicator_id]))
    return None if meta is None else meta.incremental


@pytest.mark.parametrize(
    "indicator_id", sorted(doc.declared_set("intrabar_update", "yes"))
)
def test_the_intrabar_capable_indicator_names_a_registered_incrementer(
    indicator_id: str,
) -> None:
    """増分器は 2 か所の宣言が揃って初めて効く（factory ＋ `call_binding` の latest_meta）。

    片方だけの宣言は例外を出さずに重い経路へ黙って縮退する（ISSUE-262・
    `indigators/indicator_ui/api/adapter/compute/incremental/__init__.py` の docstring）。
    本書が「足内更新 可」と言う指標は両方を持たねばならない。
    """
    # Arrange
    registered = incrementer_names()

    # Act
    named = declared_incrementer(indicator_id)

    # Assert
    assert named in registered, (indicator_id, named)


@pytest.mark.parametrize(
    "indicator_id", sorted(doc.declared_set("intrabar_update", "no"))
)
def test_the_intrabar_incapable_indicator_declares_no_incremental_archetype(
    indicator_id: str,
) -> None:
    """本書が「足内更新 不可」と言う指標は increment の archetype を宣言していない。

    §7 段 2 の対象はこの宣言で決まる。ここがずれると cvfe の更新粒度がバー確定である
    ことを表示できず、無言の縮退になる（§7・§5.2）。
    """
    # Act
    archetype = declared_archetype(indicator_id)

    # Assert
    assert archetype != "incremental", (indicator_id, archetype)


# --------------------------------------------------------------------------- #
# 3. 定数（§5.3.2）
# --------------------------------------------------------------------------- #
def test_the_document_min_gpd_events_matches_the_constant() -> None:
    """§5.3.2 / §7.1.1 の `MIN_GPD_EVENTS` = `common.gpd.MIN_GPD_EVENTS`。"""
    # Arrange
    from common.gpd import MIN_GPD_EVENTS

    # Act
    declared = doc.constant("MIN_GPD_EVENTS")

    # Assert
    assert declared == MIN_GPD_EVENTS


# --------------------------------------------------------------------------- #
# 4. 価格へ逆算できる指標（§5.5.1）
# --------------------------------------------------------------------------- #
def test_the_document_price_invertible_matches_the_breakpoint_registry() -> None:
    """§5.5.1 / §7.1.1 の `yes` = `breakpoints()` を提供する実装の集合。"""
    # Arrange
    from dashboard_ui.adapter.breakpoints import BreakpointRegistry

    registry = BreakpointRegistry()

    # Act
    invertible = registry.invertible_ids()

    # Assert
    assert doc.declared_set("price_invertible", "yes") == invertible


@pytest.mark.parametrize(
    "indicator_id", sorted(doc.declared_set("price_invertible", "no"))
)
def test_the_non_invertible_indicator_has_no_breakpoint_source(indicator_id: str) -> None:
    """§5.5.1: 除外は列挙で書かない。レジストリに**キーが無い**という形で現れる。"""
    # Arrange
    from dashboard_ui.adapter.breakpoints import BreakpointRegistry

    registry = BreakpointRegistry()

    # Act
    source = registry.resolve(indicator_id)

    # Assert
    assert source is None


# --------------------------------------------------------------------------- #
# 5. 積み上がる量か（§5.3.3）
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("indicator_id", sorted(doc.oscillator_ids()))
def test_the_document_cumulative_matches_the_series_role_table(indicator_id: str) -> None:
    """§5.3.3 / §7.1.1 の "cumulative" = 役割判定表の性質宣言。

    tickvol だけが足の中で積み上がる量であり、同じ経過割合の分布へ当てる（§5.3.3）。
    本書と宣言がずれると、積み上がらない量を経過割合の分布へ当てる（あるいはその逆の）
    配色が出る。出力は「それらしい色」のまま正しくないので、状態検証では落ちない。
    """
    # Arrange
    from dashboard_ui.adapter.series_role_table import SeriesRoleTable
    from dashboard_ui.usecase.sheet_models import SheetInstance

    instance = SheetInstance.of(
        indicator_id, "default", dict(REQUEST_PARAMS[indicator_id]),
        chart_timeframe="5m",
    )
    expected = indicator_id in doc.declared_set("cumulative", "yes")

    # Act
    spec = SeriesRoleTable().oscillator_spec(instance=instance, series_names=frozenset())

    # Assert
    assert spec is not None, f"{indicator_id}: 第 2 表のセル宣言がありません"
    assert spec.cumulative is expected


# --------------------------------------------------------------------------- #
# 6. §3.1 / §3.2 の人間向けの表と機械可読ブロックの一致
# --------------------------------------------------------------------------- #
def test_the_prose_table_of_section_3_1_lists_the_same_indicators_as_the_block() -> None:
    """§3.1 の表の indicatorId 列 = §7.1.1 の price_scale のキー。"""
    # 左辺は §3.1 の人間向けの表、右辺は §7.1.1 の機械可読ブロック。doc はどちらも
    # 本書（.md）を読むだけの reader であり、被検査コードは 1 つも呼んでいない。
    # di-ok(C3): 両辺とも本書の別々の箇所を読んだ値であり、期待値の自己生成に当たらない
    assert doc.prose_price_scale_levels().keys() == doc.price_scale_ids()


@pytest.mark.parametrize("indicator_id", sorted(doc.price_scale_ids()))
def test_the_prose_table_of_section_3_1_lists_the_same_levels_as_the_block(
    indicator_id: str,
) -> None:
    """§3.1 の表の「水準系列」列 = §7.1.1 の "levels"（写しを機械的に縛る）。"""
    # Arrange
    params = REQUEST_PARAMS[indicator_id]
    expected = doc.contract_levels(indicator_id, params)

    # Act
    prose = doc.prose_price_scale_levels()[indicator_id]

    # Assert
    assert prose == expected


def test_the_prose_of_section_3_1_lists_the_same_non_levels_as_the_block() -> None:
    """§3.1 の「価格スケールに乗らない系列」段落 = §7.1.1 の "not_levels"。

    §11-6 で人手で見つけた誤り（beta と btlm_trail_beta の取り違え）はこの形で落ちる。
    """
    # Arrange
    expected: "frozenset[str]" = frozenset()
    for indicator_id in doc.price_scale_ids():
        expected |= doc.contract_not_levels(indicator_id, REQUEST_PARAMS[indicator_id])

    # Act
    prose = doc.prose_price_scale_not_levels()

    # Assert
    assert prose == expected


def test_the_prose_table_of_section_3_2_lists_the_same_indicators_as_the_block() -> None:
    """§3.2 の表の indicatorId 列 = §7.1.1 の oscillator のキー。"""
    # di-ok(C3): 左辺は §3.2 の表・右辺は §7.1.1 のブロックで、どちらも本書を読んだ値
    assert doc.prose_oscillator_ids() == doc.oscillator_ids()


@pytest.mark.parametrize("indicator_id", sorted(doc.oscillator_ids()))
def test_the_prose_patterns_of_section_3_2_name_series_the_block_declares(
    indicator_id: str,
) -> None:
    """§3.2 の「水準系列」列のひな型が指す名前が §7.1.1 の列挙に在ること。

    §3.2 の列は完全な系列名ではなく接尾辞のひな型（`_q{pct}`）と散文（「GPD 外挿」）の
    混在なので、固定できるのは**展開できるひな型が実在の名前を指すこと**までである
    （§7.1「文章の言い換えは対象にしない」）。等号にすると散文の分だけ必ず赤くなる。
    """
    # Arrange
    params = REQUEST_PARAMS[indicator_id]
    declared = doc.contract_series_names(indicator_id, params)

    # Act
    patterns = doc.prose_oscillator_patterns(indicator_id, params)

    # Assert
    assert patterns, f"{indicator_id}: §3.2 の表からひな型を 1 つも読めていません"
    assert patterns <= declared, sorted(patterns - declared)


# --------------------------------------------------------------------------- #
# 7. v(C) の単調性（§5.5.2・性質テスト）
# --------------------------------------------------------------------------- #
#: 前進評価の素材（参照実装 `tools/measure/issue449/probe_inverse.py` と同じ足・同じ本数）。
FORWARD_REF = "jp225_tick"
FORWARD_TIMEFRAME = "5m"
FORWARD_BARS = 600


@pytest.fixture(scope="module")
def forward_window():
    from simulator.replay_ui.adapter import _indicator_ui_bridge

    bridge = _indicator_ui_bridge.load_compute()
    return bridge.dataset.load_dataframe(FORWARD_REF, FORWARD_TIMEFRAME).tail(FORWARD_BARS)


def close_grid(window) -> "list[float]":
    """決定的な終値の格子（`C < L` / 区分の内側 / 境界 / `C > H` をまたぐ）。"""
    high = float(window["high"].iloc[-1])
    low = float(window["low"].iloc[-1])
    span = max(high - low, 1.0)
    return [
        low - 2.0 * span, low, low + 0.25 * span, (low + high) / 2.0,
        low + 0.75 * span, high, high + 2.0 * span,
    ]


@pytest.mark.parametrize(
    "indicator_id", sorted(doc.declared_set("price_invertible", "yes"))
)
def test_the_forward_evaluation_is_increasing_in_the_close(
    indicator_id: str, forward_window
) -> None:
    """§5.5.2「全区分で単調増加」。

    これが崩れると、価格の交差による到達判定と指標値の交差の同値性（§6.1）が失われ、
    §6.1 の判定規約を二重に持たねばならなくなる。到達する量の系列名も**本書から**引く。
    """
    # Arrange
    from dashboard_ui.adapter.gateway.forward_evaluation_gateway import (
        ForwardEvaluationGateway,
    )

    gateway = ForwardEvaluationGateway(
        value_series_of=lambda indicator, variant, params: doc.value_series_of(indicator),
        bar_limits={FORWARD_TIMEFRAME: FORWARD_BARS},
    )

    # Act
    values = [
        gateway.value_at_close(
            indicator_id=indicator_id, variant="default",
            params=REQUEST_PARAMS[indicator_id], dataset_ref=FORWARD_REF,
            timeframe=FORWARD_TIMEFRAME, close=close,
        )
        for close in close_grid(forward_window)
    ]

    # Assert
    assert values == sorted(values), (indicator_id, values)
    assert values[0] < values[-1], (indicator_id, values)
