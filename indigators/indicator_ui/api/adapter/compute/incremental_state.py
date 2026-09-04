"""増分計算の状態保持と呼出規約（ISSUE-233・内部設計_latest増分計算.md §5.3）。

責務:
    指標ごとの増分器（``adapter.compute.incremental``）を、キー付き LRU の状態キャッシュと
    「非破壊 step / 確定時 advance」の規約で駆動する。**指標の中身は一切知らない**
    （計算式・系列名・パラメータ意味論はすべて増分器側）。

キーの単位（ISSUE-465）:
    状態のキーは「指標・variant・増分器・params」＋ **素材の識別**（どのデータの・どの足か）
    である。状態はその素材の確定プレフィクスまで進めた計算なので、別の素材へは流用できない
    （``adapt`` が不一致で ``None`` を返す）。素材を区別しないと、8 足を巡回する要求では
    足が替わるたびに全再構築が起きる。骨格（系列 metadata）は素材に依らないため、
    素材を含めないキーで共有する。

なぜキャッシュが「最適化」ではなく「仕様」か:
    増分計算は「前バーまでの状態を保持し 1 点だけ進める」計算であり、状態を持たなければ
    成立しない。キャッシュが外れても値は full と同一（再構築するだけ）であり、遅くなっても
    壊れない。

不変条件（最重要・§5.3.2）:
    足内更新は「同じ確定状態から、形成中バーを差し替えて何度でも呼ぶ」操作である。したがって
    増分器の ``emit`` は状態を **読むだけ** とし、状態の前進は確定バー到達時の ``adapt``
    （＝advance）でのみ行う。本モジュールは ``emit`` の戻り値で状態を差し替えない。

スレッド安全:
    計算プールは複数スレッドで走り得る（thread_affinity 未宣言の指標）。キャッシュの参照・
    更新のみをロックで保護する。状態オブジェクトは不変（差し替えのみ）のため、計算そのものは
    ロック外で進む。同一キーへ複数スレッドが同時に入ると状態を二重構築し得るが、構築結果は
    等価であり値は変わらない（遅くなるだけ）。
"""

from __future__ import annotations

import json
import threading
from collections import OrderedDict
from typing import Any, Protocol

from adapter.compute import incremental as _incremental_registry
from marketdata.material_identity import material_of

# 状態エントリの上限。1 つの状態は「指標インスタンス × 素材（データセット・時間足）」ごとに
# 要る（ISSUE-465）。ダッシュボードは 1 要求で 8 素材を巡回し、指標インスタンスは数十本に
# なるため、素材数ぶんの余裕が無いと LRU 追い出しで再構築が復活する（キー拡張が無効化される）。
# 超過は LRU で破棄し次回再構築する（値は変わらない・遅くなるだけ）。
_MAX_ENTRIES = 512

_LOCK = threading.Lock()
_STATES: "OrderedDict[tuple, Any]" = OrderedDict()
# 系列 JSON の metadata 骨格（name/kind/style/width/color/描画ヒント）。窓長に依らないため
# キーごとに 1 回だけ実計算（adapter.compute）から採取して再利用する。
_SKELETONS: "OrderedDict[tuple, list[dict[str, Any]]]" = OrderedDict()


class Incrementer(Protocol):
    """指標ごとの増分器が満たす契約（本モジュールが駆動する 4 面）。

    ``prepare`` 以外は ``prepare`` が返した要求オブジェクト ``req`` を受け取る。実装は
    指標 src の **公開関数のみ**を呼び、計算式を写さない（写した時点で参照実装との二重定義に
    なり ISSUE-233 の再発源になる）。
    """

    def prepare(self, df: Any, params: dict[str, Any]) -> Any:
        """(df, params) を増分計算で扱えるか判定し、扱えるなら要求オブジェクトを返す。

        扱えない（未対応パラメータ・本数不足など）ときは ``None`` を返す。呼び出し側は
        従来の full 切り出し経路へ落ちる＝挙動は 1 ビットも変わらない。
        """

    def build(self, req: Any) -> Any:
        """確定プレフィクスから状態を新規構築する（初回のみ・full 相当のコスト）。"""

    def adapt(self, state: Any, req: Any) -> Any:
        """既存状態を ``req`` へ流用する。必要なら確定バーぶん前進した **新しい** 状態を返す。

        流用できない（別系列・別パラメータ・プレフィクス不一致）ときは ``None`` を返す。
        既存状態を破壊してはならない。
        """

    def emit(self, state: Any, req: Any, skeleton: list, k: "int | None") -> "list[dict] | None":
        """確定状態＋形成中バーから末尾 K 点の系列 JSON を組む（**非破壊**）。

        組めない場合は ``None``（呼び出し側は従来経路へ落ちる）。
        """


def _params_key(params: dict[str, Any]) -> str:
    """params を決定論的な文字列キーへ（順序非依存・非 JSON 値は repr で安定化）。"""
    return json.dumps(params, sort_keys=True, default=repr, ensure_ascii=False)


def _state_key(key: tuple, df: Any) -> tuple:
    """状態のキー＝骨格のキー ＋ **素材の識別**（ISSUE-465）。

    状態は「その素材の確定プレフィクスまで進めた計算」であり、別の素材（別の時間足・別の
    データセット）へは流用できない（増分器の ``adapt`` が不一致で ``None`` を返す）。素材を
    区別しないキーで 1 つしか持たないと、素材が交互に来るたびに全再構築が起きる
    （実測 2026-08-30: 8 足巡回で末尾 1 点が 0.2〜2.7ms → 212〜374ms）。

    識別は素材そのものが運ぶ（:func:`marketdata.material_identity.material_of`）。識別を
    持たない素材（合成 DataFrame・上位足投影の畳み足）は ``None``＝従来どおり 1 つの
    入れ物を共有する（挙動は変わらない）。
    """
    return key + (material_of(df),)


def _cache_get(store: OrderedDict, key: tuple) -> Any:
    with _LOCK:
        if key not in store:
            return None
        store.move_to_end(key)
        return store[key]


def _cache_put(store: OrderedDict, key: tuple, value: Any) -> None:
    with _LOCK:
        store[key] = value
        store.move_to_end(key)
        while len(store) > _MAX_ENTRIES:
            store.popitem(last=False)


def reset() -> None:
    """キャッシュを空にする（テスト用。本番経路からは呼ばない）。"""
    with _LOCK:
        _STATES.clear()
        _SKELETONS.clear()


def stats() -> dict[str, int]:
    """保持エントリ数（テスト・診断用）。"""
    with _LOCK:
        return {"states": len(_STATES), "skeletons": len(_SKELETONS)}


def _skeleton(
    adapter: Any, compute_id: str, variant: str, df: Any, params: dict[str, Any], key: tuple
) -> "list[dict[str, Any]] | None":
    """系列 metadata の骨格（data 抜き）を実計算から採取する（キーごとに 1 回）。

    骨格を参照実装（``adapter.compute`` → 各指標 add_*）から採ることで、系列名・色・描画
    ヒントを増分器側へ書き写さない。空応答（計算不能）は増分計算の対象外を意味する。

    ``data`` は落とすが **キーは残す**（値は None）。``data`` を持たない payload
    （horizontal_line 群＝価格軸要素）と区別するためで、増分器は ``"data" in entry`` で
    「時系列データを差し替える系列か」を判定できる。
    """
    cached = _cache_get(_SKELETONS, key)
    if cached is not None:
        return cached
    series = adapter.compute(compute_id, variant, df, params)
    if not series:
        return None
    skeleton = [
        ({**s, "data": None} if "data" in s else dict(s)) for s in series
    ]
    _cache_put(_SKELETONS, key, skeleton)
    return skeleton


def compute(
    adapter: Any,
    compute_id: str,
    variant: str,
    df: Any,
    params: dict[str, Any],
    *,
    name: str,
    k: "int | None",
) -> "list[dict[str, Any]] | None":
    """増分計算で末尾 K 点の系列 JSON を返す。対象外なら ``None``（呼び出し側は従来経路へ）。

    手順:
        1. 増分器を解決し ``prepare`` で対象判定（対象外は即 None）。
        2. 骨格（系列 metadata）をキー単位で採取・再利用。
        3. 状態を LRU から引き、``adapt`` で流用（必要なら確定ぶん前進）。流用不能なら
           ``build`` で再構築。
        4. ``emit`` で末尾 K 点を組む（状態は変更しない）。
    """
    incrementer = _incremental_registry.resolve(name)
    if incrementer is None:
        return None
    req = incrementer.prepare(df, params)
    if req is None:
        return None

    key = (compute_id, variant, name, _params_key(params))
    skeleton = _skeleton(adapter, compute_id, variant, df, params, key)
    if skeleton is None:
        return None

    state_key = _state_key(key, df)
    state = _cache_get(_STATES, state_key)
    if state is not None:
        state = incrementer.adapt(state, req)
    if state is None:
        state = incrementer.build(req)
    _cache_put(_STATES, state_key, state)

    return incrementer.emit(state, req, skeleton, k)


def compute_seq(
    adapter: Any,
    compute_id: str,
    variant: str,
    df: Any,
    bars: "list[dict]",
    params: dict[str, Any],
    *,
    name: str,
    k: "int | None",
) -> "list[list[dict[str, Any]] | None] | None":
    """確定プレフィクス共通・末尾 1 本だけが違う複数時点を、``prepare`` 1 回で計算する。

    上位足投影（:mod:`adapter.compute.mtf_causal`）は、1 つの期間について「同じ確定プレフィクス
    ＋その時点まで畳んだ末尾 1 本」を時点ぶん繰り返し要求する。時点ごとに :func:`compute` を
    呼ぶと ``prepare`` が毎回プレフィクス全体の配列を作り直し、費用が「時点数 × プレフィクス長」に
    比例する（実測: 1 リクエストで 500 回・冷えた MTF 1 本 311〜428 ms＝ISSUE-450 第 5 段）。

    Args:
        df: プレフィクス ＋ ``bars[0]`` を末尾に持つ DataFrame（``compute`` へ渡すのと同じ形）。
        bars: 各時点の畳み足（``time``/``open``/``high``/``low``/``close``/``volume``）。
            ``bars[0]`` は ``df`` の末尾行と同一でなければならない。

    Returns:
        ``bars`` と同数の系列 JSON（各要素は :func:`compute` の戻りと同形）。
        増分器が ``replace_last`` を持たない・対象外・入力不正のときは ``None``
        （呼び出し側は時点ごとの従来経路へ落ちる＝**黙って値を変えない**）。
    """
    if not bars:
        return []
    incrementer = _incremental_registry.resolve(name)
    if incrementer is None:
        return None
    replace_last = getattr(incrementer, "replace_last", None)
    if replace_last is None:
        return None                     # 未対応の増分器は従来経路（劣化を隠さない）
    req = incrementer.prepare(df, params)
    if req is None:
        return None

    key = (compute_id, variant, name, _params_key(params))
    skeleton = _skeleton(adapter, compute_id, variant, df, params, key)
    if skeleton is None:
        return None

    state_key = _state_key(key, df)
    state = _cache_get(_STATES, state_key)
    if state is not None:
        state = incrementer.adapt(state, req)
    if state is None:
        state = incrementer.build(req)
    _cache_put(_STATES, state_key, state)

    out: "list[list[dict[str, Any]] | None]" = []
    for index, bar in enumerate(bars):
        if index > 0:                   # bars[0] は df の末尾行＝prepare 済み
            stepped = replace_last(req, bar)
            if stepped is None:
                return None             # 途中で扱えなくなったら全体を従来経路へ委ねる
            req = stepped
        out.append(incrementer.emit(state, req, skeleton, k))
    return out
