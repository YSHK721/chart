"""増分器状態キャッシュの**計算量テスト**（ISSUE-465・CLAUDE.md 絶対命令 2026-08-28）。

固定するのは出力の正しさではなく **無駄の不在** である。

    状態の再構築（build）− 素材の数 = 0

増分計算は「前バーまでの状態を保持して 1 点だけ進める」計算であり、状態が別の素材
（別の時間足・別のデータ）のものであれば流用できない（増分器の ``adapt`` が確定
プレフィクスの不一致で ``None`` を返す）。したがって **素材を区別しないキー**で状態を
1 つしか持たないと、素材が替わるたびに全再構築が起きる。出力はどちらでも同じなので
状態検証（値を見る通常のテスト）では原理的に落ちない——実測 2026-08-30 では 8 足を
巡回する要求で末尾 1 点が 0.2〜2.7 ms から 212〜374 ms へ悪化していた（ISSUE-465）。

回数そのもの（「N 回呼ばれること」）は固定しない。それをやると浪費が仕様へ昇格する。
ここで固定するのは次の 2 点だけ:

  1. 再構築は素材 1 つにつき 1 回で頭打ち（超過＝捨てられる計算が 0 件）。
  2. 再構築の数は**素材の数**で決まり、要求の回数では増えない（オーダーの表明・2 点で固定）。

構造: Arrange-Act-Assert（AAA）。
"""
from __future__ import annotations

import pytest

from adapter.compute import incremental as incremental_registry
from adapter.compute import incremental_state
from marketdata import dataset
from marketdata.material_identity import material_of

#: 素材として使う実データ（sample は小さく、足ごとに本数も期間も違う＝別の素材である）。
REF = "sample"
SPY = "spy_material"


class _CountingIncrementer:
    """発行された「状態の構築」を数える Test Spy（実物の増分器と同型の契約）。

    実物（例: :class:`adapter.compute.incremental.moving_averages.MovingAveragesIncrementer`）は
    ``adapt`` で確定プレフィクスの一致を見て流用可否を決める。別素材の状態は必ず不一致に
    なるため ``None``＝再構築になる。この Spy は「素材が違えば流用できない」という
    その一点だけを写し、計算式は持たない。
    """

    def __init__(self) -> None:
        self.builds: "list[tuple]" = []
        self.emits = 0

    @staticmethod
    def _material(df) -> tuple:
        """テスト側が持つ「別の素材か」の判定（本数と先頭時刻）。実装の識別とは独立。"""
        return (len(df), int(df.index[0].value))

    def prepare(self, df, params):
        return {"material": self._material(df)}

    def build(self, req):
        self.builds.append(req["material"])
        return {"material": req["material"]}

    def adapt(self, state, req):
        return state if state["material"] == req["material"] else None

    def emit(self, state, req, skeleton, k):
        self.emits += 1
        return [{**skeleton[0], "data": [{"time": 1, "value": 1.0}]}]


class _SkeletonAdapter:
    """系列 metadata の骨格を返すだけの計算面（骨格採取は素材に依らない・窓長にも依らない）。"""

    def __init__(self) -> None:
        self.calls = 0

    def compute(self, compute_id, variant, df, params):
        self.calls += 1
        return [{"name": "X", "kind": "line", "data": []}]


@pytest.fixture()
def spy(monkeypatch) -> _CountingIncrementer:
    incremental_state.reset()
    incrementer = _CountingIncrementer()
    monkeypatch.setitem(incremental_registry._INSTANCES, SPY, incrementer)
    yield incrementer
    incremental_state.reset()


def _materials(timeframes: "list[str]") -> "list":
    """本番と同じ供給経路から素材を得る（識別を載せるのは供給側の責務）。"""
    return [dataset.load_dataframe(REF, tf) for tf in timeframes]


def _cycle(spy: _CountingIncrementer, materials: "list", rounds: int) -> None:
    adapter = _SkeletonAdapter()
    for _ in range(rounds):
        for material in materials:
            incremental_state.compute(
                adapter, "spy_indicator", "default", material, {},
                name=SPY, k=1,
            )


def test_two_materials_in_turn_rebuild_the_state_once_each(spy) -> None:
    # Arrange: 別の素材 2 つ（同じデータの別の足）。
    materials = _materials(["1D", "1W"])

    # Act: 交互に 4 巡（＝8 要求）。
    _cycle(spy, materials, rounds=4)

    # Assert: 発行した再構築のうち、素材数を超えるぶん（＝捨てられる計算）が 0 件。
    assert spy.emits == 8                                   # 検査が空虚でないこと
    assert len(spy.builds) - len(materials) == 0


def test_the_rebuilds_are_bounded_by_the_materials_not_by_the_requests(spy) -> None:
    """オーダーの表明: 要求を 3 倍にしても再構築は増えない（素材数だけで決まる）。"""
    # Arrange
    materials = _materials(["1D", "1W", "1M"])

    # Act: 同じ素材集合を 12 巡（＝36 要求。前段の 2 点目）。
    _cycle(spy, materials, rounds=12)

    # Assert
    assert spy.emits == 36
    assert len(spy.builds) - len(materials) == 0


def test_the_skeleton_is_taken_once_because_it_does_not_depend_on_the_material(spy) -> None:
    """骨格（系列 metadata）は素材に依らない。素材ごとに実計算を発行してはならない。

    骨格の採取は ``adapter.compute`` の**全件計算**であり、素材ごとに採ると 8 足で 8 回の
    全件計算になる（状態の再構築を消した分より高くつく）。キーの拡張は状態の側だけに効く。
    """
    # Arrange
    materials = _materials(["1D", "1W", "1M"])
    adapter = _SkeletonAdapter()

    # Act
    for material in materials:
        incremental_state.compute(
            adapter, "spy_indicator", "default", material, {}, name=SPY, k=1,
        )

    # Assert: 必要な 1 回だけ（素材ごとの再採取は捨てられる全件計算になる）。
    assert adapter.calls == 1


def test_the_material_identity_survives_the_windowing_that_the_callers_do() -> None:
    """呼出し側が素材へ施す加工を通しても識別が残る（残らなければ無言で従来の費用へ戻る）。

    加工の出所は実コードである:
      - ``df.tail(limit)``   … 窓の本数制限（live_tick_tails_controller._load_window ほか）
      - ``df.copy()``        … 毎ティックの差し替え用の複製（live_tick_tails.make_tail_at）
      - ``df.iloc[:-1]``     … 確定足と形成中足の分割（dashboard の素材分割）
      - ``loc`` 追加＋``sort_index`` … 形成中バーの注入（forming_bar.apply_forming_bar）
    """
    # Arrange
    frame = dataset.load_dataframe(REF, "1D")
    identity = material_of(frame)

    # Act
    injected = frame.copy()
    injected.loc[injected.index[-1] + (injected.index[-1] - injected.index[-2])] = (
        injected.iloc[-1]
    )
    injected = injected.sort_index()

    # Assert
    assert identity is not None
    assert material_of(frame.tail(10)) == identity
    assert material_of(frame.copy()) == identity
    assert material_of(frame.iloc[:-1]) == identity
    assert material_of(injected) == identity


def test_a_material_that_never_passed_the_supplier_has_no_identity() -> None:
    """供給側を通っていない素材（増分器へ直に渡される合成 DataFrame）は識別を持たない。

    識別はキーであって正しさの担保ではない（値は増分器の ``adapt`` が守る）。識別の無い
    素材は従来どおり 1 つの入れ物を共有する＝既存経路の挙動は変わらない。
    """
    # Arrange
    bare = dataset.load_dataframe(REF, "1D").copy()
    bare.attrs = {}

    # Act / Assert
    assert material_of(bare) is None
