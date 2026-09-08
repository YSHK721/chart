"""e2e 検定へ「固定素材の bridge」と「固定時計」を配る（ISSUE-503 事象 B）。

## なぜ在るか

`test_serve_dashboard_smoke.py` の計算量の表明（同じ要求を繰り返しても確定素材の
発行は増えない）は、**素材の版（epoch）が観測の間ずっと不変である**ことを前提にする。
本番の bridge はライブの `jp225_tick` を読むため、市場稼働中は観測の途中で 1m 足が
1 本確定し、版が進んで素材が正しく作り直される。仕様どおりの再構築を「増えない」と
測っているので、検定は**目的と無関係な理由で**赤くなる（ISSUE-503 事象 B・2026-09-07
に 1 回赤を実測）。

抜本策は「症状の出る条件を避ける」ことではなく、**時刻と素材の進行を検定が支配する**
ことである。素材を検定側で合成し、時計を固定値にすれば、版が進む経路そのものが構造から
消える。前例は `dashboard_ui/tests/complexity/test_material_shared_across_epoch.py` の
Material / BridgeSpy（周期前進・遡り訂正・形成中足移動をテストが操作する）で、本 fixture は
それを**殻ごしの e2e** へ持ち上げたものである。

## 何を固定し、何を固定しないか

固定するのは素材と時計だけである。計算面（full_compute / latest_compute / adapter /
compute_error / catalog_param_scopes）はライブ core の実体をそのまま再輸出する——ここを
偽物にすると「結線が正しければ実際に計算が通る」という e2e の目的そのものが消える。

巻き戻し面（forming_bar_module）は**ダブルを載せる**。実体は tick parquet を読むので
載せられないが、載せないと gateway が属性の不在で素通しし、注入した時計へ到達しない
——値を渡すだけで誰も読まない口は結線が無検定であり、渡す側を消しても誰も落ちない
（是正レビュー R-1 の実測: CLOCK CALLS = 0）。ダブルは「その周期に tick が無い」既存経路へ
倒すだけで、素材を 1 行も変えない。

## data/ を読まない

素材は合成である（依頼者制約: data/ 配下と既存 CSV / parquet は改変も依存もしない）。
値は決定的な擬似乱数（線形合同法）で、実行のたびに同じ列になる。
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pandas as pd
import pytest

from marketdata.tf_meta import period_start_unix

#: 表示に使うデータセット参照（銘柄仕様の引き当てはライブと同一の台帳を通す）。
REF = "jp225_tick"

#: 素材の基準時刻（2026-08-17 21:50:00 UTC）。
#:
#: 各時間足の行は **この時刻を含む周期の始端**から並べる（load_dataframe が導く）。境界に
#: 載る載らないを定数の側に書かない: 定数へ「分・5 分・15 分の境界」のような主張を書くと、
#: 時間足を 1 つ増やした日に文章だけが古くなる（実測 2026-09-08: 本コメントの旧版は 15 分
#: 境界に載るとしていたが実際には載っておらず、日付表記も別ファイルからの写しで誤っていた）。
#: 整列の判定は marketdata.tf_meta.period_start_unix が唯一源であり、ライブ側と同じ規約になる。
START = 1_787_003_400

#: 合成する足の本数（e2e の既存 bar_limits と同じ 600 本）。
BARS = 600

#: 固定時計（UNIX 秒）。素材の末尾より後の一点で、実行時刻に依存しない。
FIXED_NOW = float(START + BARS * 60)

#: 時間足 → 1 本あたりの秒数。束が別の足を要求しても素材を出せるようにする
#: （出せないと検定は目的と無関係な理由＝供給不能で赤くなる）。
_STEP = {
    "1m": 60, "5m": 300, "15m": 900, "1h": 3600,
    "4h": 14400, "1D": 86400, "1W": 604800, "1M": 2592000,
}

#: 合成価格の中心（JP225 の実勢帯。価格ラダーの量子化幅が実銘柄仕様で意味を持つ帯にする）。
_BASE_PRICE = 43000.0


def _walk(rows: int, seed: int) -> "list[float]":
    """決定的な擬似乱数の価格列（線形合同法・Numerical Recipes の係数）。

    定数列にすると分位が退化してオシレータのセルが空になり、検定が「縮退で緑」になる。
    ここが作るのは向きの変わる列であって、実データの模倣ではない。
    """
    state = seed
    price = _BASE_PRICE
    out: "list[float]" = []
    for _ in range(rows):
        state = (1_664_525 * state + 1_013_904_223) % (2 ** 32)
        price += (state / (2 ** 32) - 0.5) * 20.0
        out.append(round(price, 1))
    return out


class FixedMaterialBridge:
    """固定素材を配り、確定素材の発行（full_compute）を数える bridge 兼 Test Spy。

    dataset 面だけを合成で置き換え、計算面はライブ core の実体を通す。数えるのは
    full_compute の発行だけである——形成中足の末尾 1 点（latest_compute）は段 2 の
    観測値更新であり、要求ごとに出るのが仕様である。
    """

    def __init__(self, compute: Any, *, rows: int = BARS) -> None:
        self._compute = compute
        self._rows = int(rows)
        self._frames: "dict[str, pd.DataFrame]" = {}
        self.forming_bar_module = FormingBarModuleDouble()
        self.full_calls: "list[str]" = []
        self.latest_calls: "list[str]" = []

    # --------------------------------------------------------------- dataset 面
    def is_known(self, ref: str) -> bool:
        return ref == REF

    def is_known_timeframe(self, timeframe: str) -> bool:
        return timeframe in _STEP

    def load_dataframe(self, ref: str, timeframe: str = "1m") -> "pd.DataFrame":
        """その時間足の素材（**呼ぶたびに同じ内容**＝版が進まない）。"""
        cached = self._frames.get(timeframe)
        if cached is not None:
            return cached
        step = _STEP[timeframe]
        # 行はその時間足の周期の始端に載せる（整列の唯一源はライブ側と同じ period_start_unix）。
        first = period_start_unix(START, timeframe)
        times = [first + index * step for index in range(self._rows)]
        closes = _walk(self._rows, seed=step)
        opens = [closes[0], *closes[:-1]]
        self._frames[timeframe] = pd.DataFrame(
            {
                "open": opens,
                "high": [max(o, c) + 3.0 for o, c in zip(opens, closes)],
                "low": [min(o, c) - 3.0 for o, c in zip(opens, closes)],
                "close": closes,
                "volume": [100.0] * self._rows,
            },
            index=pd.to_datetime(times, unit="s"),
        )
        return self._frames[timeframe]

    # --------------------------------------------------------------- 計算面
    def full_compute(self, adapter, indicator_id, variant, frame, params):
        self.full_calls.append(indicator_id)
        return self._compute.full_compute(adapter, indicator_id, variant, frame, params)

    def latest_compute(self, adapter, indicator_id, variant, frame, params):
        self.latest_calls.append(indicator_id)
        return self._compute.latest_compute(
            adapter, indicator_id, variant, frame, params
        )

    def namespace(self) -> SimpleNamespace:
        """Composition Root へ渡す bridge。

        巻き戻し面はダブルを載せる（実体は tick parquet を読むので載せられない）。載せないと
        gateway が属性の不在で素通しし、**注入した時計へ到達しない**＝時計の結線が無検定に
        なる（是正レビュー R-1）。ダブルは素材を 1 行も変えない（FormingBarModuleDouble）。
        """
        return SimpleNamespace(
            dataset=self,
            adapter=self._compute.adapter,
            full_compute=self.full_compute,
            latest_compute=self.latest_compute,
            compute_error=self._compute.compute_error,
            catalog_param_scopes=self._compute.catalog_param_scopes,
            forming_bar_module=self.forming_bar_module,
        )


class FixedClock:
    """進まない時計（読まれた回数を数える Test Spy）。

    回数を数えるのは、注入した時計が**実際に読まれている**ことを検定が固定できるように
    するためである。読まれない口へ値を渡しても結線は無検定のままで、渡す側を消しても
    誰も落ちない（ISSUE-503 の是正レビュー R-1 の実測: CLOCK CALLS = 0）。
    """

    def __init__(self, value: float = FIXED_NOW) -> None:
        self.value = float(value)
        self.reads = 0

    def __call__(self) -> float:
        self.reads += 1
        return self.value


class FormingBarModuleDouble:
    """表示時点への巻き戻し面のダブル（最小体）。

    本体（ライブ core の時点指定 fold）は tick parquet を読む。検定でそれを読むと素材が
    実データに戻ってしまうので、**周期に tick が無いときの既存経路**をそのまま使う
    ——`forming_bar` が None を返すと、gateway は「遅延時点より後の周期の行だけ落とす」
    に倒れる（合成素材はすべて遅延時点より前なので 1 行も落ちない＝素材は不変）。

    このダブルが在ることで巻き戻しの入口が開き、注入した時計が読まれる。無いと gateway は
    属性の不在で素通しし、時計へ到達しない。
    """

    def __init__(self) -> None:
        self.calls: "list[tuple[str, str, int]]" = []

    def forming_bar(self, ref: str, timeframe: str, now_unix: int):
        self.calls.append((ref, timeframe, int(now_unix)))
        return None


@pytest.fixture(scope="session")
def live_compute() -> Any:
    """ライブ core の計算面（素材以外はここから借りる）。"""
    from indigators.indicator_ui import api_loader

    return api_loader.load_compute()


@pytest.fixture()
def new_fixed_bridge(live_compute):
    """固定素材の bridge を要求ぶんだけ作る工場。

    オーダーの 2 点表明は殻を 2 つ立てるので、bridge も 2 つ要る（1 つを共有すると
    2 点目の発行数に 1 点目が混ざり、差が測れない）。
    """
    return lambda: FixedMaterialBridge(live_compute)


@pytest.fixture()
def fixed_bridge(new_fixed_bridge) -> FixedMaterialBridge:
    """固定素材の bridge を 1 つ（1 点だけ測る検定用）。"""
    return new_fixed_bridge()


@pytest.fixture()
def fixed_clock() -> FixedClock:
    """固定時計（呼んでも進まない・読まれた回数を数える）。"""
    return FixedClock()


@pytest.fixture(scope="module")
def shared_fixed_bridge(live_compute) -> FixedMaterialBridge:
    """module 共有の固定素材 bridge（殻を 1 つだけ立てて読む検定群のため）。

    発行数を測る検定は殻を専有する必要がある（共有すると素材が既に温まっていて初回発行が
    0 になる）が、**応答の中身**を読むだけの検定は殻を共有してよい。
    """
    return FixedMaterialBridge(live_compute)


@pytest.fixture(scope="module")
def shared_fixed_clock() -> FixedClock:
    """module 共有の固定時計。"""
    return FixedClock()
