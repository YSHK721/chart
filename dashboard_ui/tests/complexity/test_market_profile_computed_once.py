"""計算量（絶対命令 CLAUDE.md §4.1）: MP プロファイルは **epoch 単位で 1 回**しか作らない。

なぜ状態検証と別に要るか:
    プロファイル（1D×60 本の TPO 畳み込み）は確定足だけから決まるので、epoch の中では
    不変である。にもかかわらず毎要求・毎行作り直しても**出力は正しいまま**であり、
    状態検証では原理的に落ちない（ISSUE-450 / ISSUE-457 / ISSUE-464 と同型の
    「作ってから捨てる」欠陥）。したがって**回数**を数える。時間は測らない
    （マシン負荷で揺れ、閾値が緩んで浪費を通す）。

固定するもの（**回数そのものは焼き込まない**）:
    - 不変量 A（無駄の不在）: 素材が変わらない限り、要求を繰り返しても追加発行は 0。
    - 不変量 B（オーダーの表明）: 要求する価格（＝ラダーの行数）を増やしても発行は増えない。
    - 不変量 C（発行 − 使用 = 0）: 発行したプロファイルはすべて出力に使われる。
    - 不変量 D（鮮度）: 確定 1D 足が進んだら作り直し、以後の繰り返しでは追加 0 に戻る。
"""
from __future__ import annotations

from dashboard_ui.adapter.gateway.market_profile_gateway import (
    MP_TIMEFRAME,
    MarketProfileGateway,
)
from dashboard_ui.adapter.gateway.material_store import MaterialStore
from dashboard_ui.domain.bar import Bar

REF = "jp225_tick"
#: 2026-08-01 00:00:00 UTC（1D の境界に載る時刻）。
START = 1_785_542_400
DAY = 86_400
#: 価格域 [100, 160] を 6 bin（幅 10）へ割る素材。行価格はこの範囲内から採る。
PRICE_MIN = 100.0
PRICE_MAX = 160.0
BINS = 6


class Material:
    """1D の素材（確定足 ＋ 形成中足）。テストから周期を進め、形成中足だけを動かせる。"""

    def __init__(self, *, rows: int) -> None:
        self.times = [START + index * DAY for index in range(rows)]
        self.closes = [120.0 + index * 0.1 for index in range(rows)]

    def advance_epoch(self) -> None:
        """周期を 1 つ進める（新しい確定足が現れる＝確定素材が変わる）。"""
        self.times.append(self.times[-1] + DAY)
        self.closes.append(self.closes[-1] + 0.1)

    def move_forming_bar(self, close: float) -> None:
        """形成中足だけを動かす（周期は進まない＝確定素材は変わらない）。"""
        self.closes[-1] = float(close)

    def bars(self) -> "tuple[Bar, ...]":
        return tuple(
            Bar(time=time, open=close, high=close + 5.0, low=close - 5.0, close=close)
            for time, close in zip(self.times, self.closes)
        )


class BarPortSpy:
    """P-2 の代役。末尾は**形成中の足**（P-2 の契約どおり `forming_bar` と同一物）。"""

    def __init__(self, material: Material) -> None:
        self._material = material

    def bars(self, *, dataset_ref, timeframe):
        return self._material.bars() if timeframe == MP_TIMEFRAME else ()

    def forming_bar(self, *, dataset_ref, timeframe, now_unix):
        supplied = self.bars(dataset_ref=dataset_ref, timeframe=timeframe)
        return supplied[-1] if supplied else None


class ProfileSpy:
    """MP 計算面の Test Spy。**数えるのはこの面の発行だけ**である。

    発行ごとに違う norm を返す＝出力を見れば「どの発行に由来するか」が一意に読める
    （不変量 C の「発行 − 使用 = 0」を出力側から検算するための仕掛け）。
    """

    def __init__(self) -> None:
        self.calls: "list[tuple[int, ...]]" = []

    def __call__(self, candles, *, n_bins):
        serial = len(self.calls) + 1
        self.calls.append(tuple(int(candle["time"]) for candle in candles))
        norm = round(0.001 * serial, 4)
        return {
            "bins": [
                {"price": PRICE_MIN + (index + 0.5) * (PRICE_MAX - PRICE_MIN) / BINS,
                 "tpo": 1, "norm": norm}
                for index in range(BINS)
            ],
            "poc": 125.0,
            "va_low": 115.0,
            "va_high": 145.0,
            "price_min": PRICE_MIN,
            "price_max": PRICE_MAX,
            "tpo_units": len(candles),
            "n_bins": BINS,
        }


def prices(count: int) -> "tuple[float, ...]":
    """ラダーの行価格の代役（すべてプロファイルの範囲内）。"""
    span = (PRICE_MAX - PRICE_MIN) * 0.98
    return tuple(
        PRICE_MIN + 0.01 * span + span * index / max(1, count - 1) for index in range(count)
    )


def request(spy: ProfileSpy, material: Material, store: MaterialStore, *, rows: int = 3):
    """要求 1 件ぶん（**口は要求ごとに組み直す**——共有するのは素材ストアだけ）。"""
    gateway = MarketProfileGateway(
        bar_port=BarPortSpy(material), store=store, compute=spy,
    )
    return gateway.norms_at(
        dataset_ref=REF, prices=prices(rows), now_unix=material.times[-1],
    )


# ------------------------------------------------------- 不変量 A: 無駄の不在
def test_repeating_the_same_request_issues_no_additional_profile() -> None:
    """epoch が進まない限り、要求を何回繰り返しても発行は増えない。

    オーダーの表明（2 点固定）: 繰り返し数 5 / 20 のどちらでも**追加は 0**。回数そのものは
    焼き込まない（焼き込むと浪費が仕様へ昇格する）。
    """
    additional = {}
    for repeats in (5, 20):
        material = Material(rows=90)
        spy = ProfileSpy()
        store = MaterialStore()
        request(spy, material, store)          # 1 回目（この epoch の素材を作る）
        warmed = len(spy.calls)
        for _ in range(repeats):
            request(spy, material, store)
        additional[repeats] = len(spy.calls) - warmed

    assert additional[5] == 0
    assert additional[20] == 0


def test_a_moving_forming_bar_issues_no_additional_profile() -> None:
    """形成中足が動いただけでは作り直さない（更新粒度は 1D バー確定・§7 段 2）。"""
    material = Material(rows=90)
    spy = ProfileSpy()
    store = MaterialStore()
    request(spy, material, store)
    warmed = len(spy.calls)

    for tick in range(20):
        material.move_forming_bar(130.0 + tick)
        request(spy, material, store)

    assert len(spy.calls) - warmed == 0


# --------------------------------------------- 不変量 B: 発行は出力量に依らない
def test_asking_for_more_row_prices_does_not_issue_more_profiles() -> None:
    """オーダーの表明（2 点固定）: 行数を増やしても発行は増えない（行ごとに畳まない）。"""
    issued = {}
    for rows in (3, 71):
        material = Material(rows=90)
        spy = ProfileSpy()
        request(spy, material, MaterialStore(), rows=rows)
        issued[rows] = len(spy.calls)

    assert issued[3] == issued[71]


def test_a_longer_material_does_not_issue_more_profiles() -> None:
    """オーダーの表明（2 点固定）: 素材の本数を増やしても発行は増えない（窓は末尾だけ）。"""
    issued = {}
    for rows in (90, 900):
        spy = ProfileSpy()
        request(spy, Material(rows=rows), MaterialStore())
        issued[rows] = len(spy.calls)

    assert issued[90] == issued[900]


# ----------------------------------------------- 不変量 C: 発行 − 使用 = 0
def test_every_issued_profile_is_used_by_the_output() -> None:
    """発行したプロファイルが 1 つでも出力に現れなければ、それは捨てた計算である。"""
    material = Material(rows=90)
    spy = ProfileSpy()
    store = MaterialStore()

    outputs = []
    for step in range(3):
        outputs.append(request(spy, material, store))
        outputs.append(request(spy, material, store))
        material.advance_epoch()

    used = {norm for norms in outputs for norm in norms if norm is not None}
    issued = {round(0.001 * (serial + 1), 4) for serial in range(len(spy.calls))}
    assert issued - used == set()


# ------------------------------------------------------------ 不変量 D: 鮮度
def test_a_new_confirmed_daily_bar_is_rebuilt_once_and_then_settles() -> None:
    """新しい確定足で作り直し、以後の繰り返しでは追加 0 に戻る（2 点固定）。"""
    additional = {}
    for repeats in (3, 12):
        material = Material(rows=90)
        spy = ProfileSpy()
        store = MaterialStore()
        request(spy, material, store)
        material.advance_epoch()
        request(spy, material, store)          # 新しい epoch の素材を作る
        warmed = len(spy.calls)
        for _ in range(repeats):
            request(spy, material, store)
        additional[repeats] = len(spy.calls) - warmed

    assert additional[3] == 0
    assert additional[12] == 0


def test_the_output_follows_the_new_confirmed_bar_instead_of_the_stale_profile() -> None:
    """共有と引き換えに古い素材を配らない（版が変わったら出力も変わる）。"""
    material = Material(rows=90)
    spy = ProfileSpy()
    store = MaterialStore()
    before = request(spy, material, store)

    material.advance_epoch()
    after = request(spy, material, store)

    assert len(spy.calls) > 1
    assert after != before
    assert spy.calls[-1][-1] == material.times[-2]   # 窓の末尾＝新しい確定足


# ------------------------------- 不変量 E: 共有ストア上で他の素材を押し出さない
# 実測 2026-09-06: 鍵を P-1 と同じ `(dataset_ref, timeframe)` に取ると、版の定義が違うため
#   互いの素材を毎要求押し出し合った（素材不変の 5 要求で P-1 の full 発行 10 回・MP の
#   畳み込み 5 回。どちらも 1 回で足りる）。**出力は正しいまま**なので状態検証では落ちない
#   ——ISSUE-450 / ISSUE-457 と同型である。共有ストアは共有したまま、鍵の名前空間を分ける。
class BridgeSpy:
    """dataset ＋ 計算面の Test Spy（P-1 の発行だけを数える）。"""

    def __init__(self, material: Material) -> None:
        self._material = material
        #: 確定素材の発行だけを数える。形成中足の末尾 1 点（`latest_compute`）は**毎要求
        #: 出るのが正しい**（鮮度）ので別に記録し、無駄の勘定へ混ぜない。
        self.full_calls: "list[str]" = []
        self.latest_calls: "list[str]" = []

    def is_known(self, ref) -> bool:
        return ref == REF

    def is_known_timeframe(self, timeframe) -> bool:
        return timeframe == MP_TIMEFRAME

    def load_dataframe(self, ref, timeframe=None):
        import pandas as pd

        closes = list(self._material.closes)
        return pd.DataFrame(
            {
                "open": closes,
                "high": [close + 5.0 for close in closes],
                "low": [close - 5.0 for close in closes],
                "close": closes,
                "volume": [1.0] * len(closes),
            },
            index=pd.to_datetime(self._material.times, unit="s"),
        )

    def full_compute(self, adapter, indicator_id, variant, df, params):
        self.full_calls.append(indicator_id)
        return [_line(indicator_id, df)]

    def latest_compute(self, adapter, indicator_id, variant, df, params):
        self.latest_calls.append(indicator_id)
        return [_line(indicator_id, df.tail(1))]

    def namespace(self):
        from types import SimpleNamespace  # noqa: PLC0415（Test Spy の局所依存）

        return SimpleNamespace(
            dataset=self, adapter=object(), full_compute=self.full_compute,
            latest_compute=self.latest_compute, compute_error=(),
        )


def _line(name: str, df) -> dict:
    seconds = df.index.values.astype("datetime64[s]").astype("int64").tolist()
    return {
        "name": name, "kind": "line",
        "data": [{"time": int(time), "value": float(close)}
                 for time, close in zip(seconds, df["close"].tolist())],
    }


def test_the_profile_does_not_evict_the_series_material_it_shares_the_store_with() -> None:
    """同じ 1D 素材を使う 2 人が同じストアに同居しても、互いの発行を増やさない（2 点固定）。"""
    from dashboard_ui.adapter.gateway.indicator_ui_compute_gateway import (
        IndicatorUiComputeGateway,
    )

    additional = {}
    for repeats in (5, 20):
        material = Material(rows=90)
        bridge = BridgeSpy(material)
        profile = ProfileSpy()
        store = MaterialStore()

        def one_request() -> None:
            # 実運用と同じ: 口は要求ごとに組み直し、素材ストアだけを共有する。
            series = IndicatorUiComputeGateway(bridge=bridge.namespace(), store=store)
            series.full_series(indicator_id="osc", variant="default", params={},
                               dataset_ref=REF, timeframe=MP_TIMEFRAME)
            MarketProfileGateway(
                bar_port=series, store=store, compute=profile,
            ).norms_at(dataset_ref=REF, prices=prices(3), now_unix=material.times[-1])

        one_request()                       # 1 回目（この epoch の素材を作る）
        warmed = (len(bridge.full_calls), len(profile.calls))
        for _ in range(repeats):
            one_request()
        additional[repeats] = (
            len(bridge.full_calls) - warmed[0], len(profile.calls) - warmed[1],
        )

    assert additional[5] == (0, 0)
    assert additional[20] == (0, 0)
