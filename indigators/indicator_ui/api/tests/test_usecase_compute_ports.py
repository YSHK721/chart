"""ISSUE-182 item4: usecase/compute_indicators の協調子契約（Protocol）の回帰ガード。

事象（是正前・実測）: :func:`usecase.compute_indicators.compute_indicators` の 5 依存のうち
契約が定義されていたのは ``dataset_port``（:class:`DatasetPort`）のみで、残りは ``Any`` /
``Callable[..., list]`` / ``type``＝**契約未定義**だった（``indigators/PORTING_GUIDE.md`` §「境界は
Protocol で定義する」と不整合）。usecase は「注入される協調子が何を満たすべきか」を宣言せず、
欠落は実際に呼ばれるまで検出されない。

本モジュールは以下を固定する:
  - 全協調子パラメータが Port 型で注釈されている（``Any`` / 素の ``Callable`` が残らない）。
  - 各 Port のメンバ集合が **usecase が実際に使う分だけ**である（ISP）。
  - 本番の具象（``IndicatorComputeAdapter`` / ``forming_bar`` module / ``full_compute`` /
    ``latest_compute`` / ``ComputeError``）が対応する Port を満たす。
  - テストの fake 協調子も同じ Port を満たす（ISSUE-177 と同方針: テストを緩めるのではなく
    fake を Port 準拠へ拡充する）。

注意: ``compute_indicators`` に ``isinstance`` 強制は入れない（挙動不変が要件）。契約は型注釈と
本ガードで固定し、実行時の分岐は増やさない（参照実装 ``dataset_port`` と同じ扱い）。
"""

from __future__ import annotations

import inspect

from usecase.compute_indicators import compute_indicators
from usecase.compute_ports import (
    ComputeDispatchPort,
    ComputeErrorPort,
    FormingBarPort,
    IndicatorComputePort,
    LatestComputeDispatchPort,
)
from usecase.dataset_port import DatasetPort

def _annotation_of(param: inspect.Parameter) -> str:
    """注釈をソース上のクォート様式に依存しない形へ正規化する。

    ``from __future__ import annotations``（PEP 563）配下では注釈は**ソーステキスト**として
    保持されるため、``x: "Foo"`` は ``'Foo'``（クォート込み）になる。本ガードが固定したいのは
    「どの Port 型か」であってクォート様式ではないので、外側のクォートを剥がして比較する。
    """
    text = str(param.annotation)
    if len(text) >= 2 and text[0] == text[-1] and text[0] in ("'", '"'):
        return text[1:-1]
    return text


#: 是正後に期待する協調子パラメータの注釈（Port 型名）。
_EXPECTED_ANNOTATIONS = {
    "dataset_port": "Optional[DatasetPort]",
    "compute_adapter": "IndicatorComputePort",
    "forming_bar": "FormingBarPort",
    "full_compute": "ComputeDispatchPort",
    "latest_compute": "LatestComputeDispatchPort",
    "compute_error": "type[ComputeErrorPort]",
}


# --------------------------------------------------------------------------- #
# 契約が「定義されている」こと
# --------------------------------------------------------------------------- #
def test_every_collaborator_parameter_is_typed_by_a_port():
    """協調子 5 依存すべてが Port 型で注釈される（``Any`` / 素の ``Callable`` を残さない）。"""
    params = inspect.signature(compute_indicators).parameters
    actual = {name: _annotation_of(p) for name, p in params.items() if name != "request"}
    assert actual == _EXPECTED_ANNOTATIONS


def test_ports_declare_only_the_members_the_usecase_uses():
    """各 Port のメンバは usecase の実使用分だけ（ISP）。推測でメソッドを増やさない。"""
    # forming_bar: usecase が呼ぶのは resolve_now_unix / apply_forming_bar の 2 つのみ。
    #   （serve_candles が使う rollup_forming_bar 等 5 メソッドは本 Port の契約外＝別クライアント。）
    assert set(FormingBarPort.__protocol_attrs__) == {"resolve_now_unix", "apply_forming_bar"}
    # compute_adapter: ディスパッチが呼ぶ adapter.compute のみ。
    assert set(IndicatorComputePort.__protocol_attrs__) == {"compute"}
    # ディスパッチ 2 種は callable 契約。
    assert set(ComputeDispatchPort.__protocol_attrs__) == {"__call__"}
    assert set(LatestComputeDispatchPort.__protocol_attrs__) == {"__call__"}
    # 例外型は error_type / message を持つ（ComputeResult へ載せる 2 属性）。
    assert set(ComputeErrorPort.__protocol_attrs__) == {"error_type", "message"}


def test_dispatch_ports_differ_by_the_measured_min_tail_keyword():
    """full / latest のディスパッチ契約は実測シグネチャどおり分かれる（``min_tail`` は latest のみ）。"""
    full_sig = inspect.signature(ComputeDispatchPort.__call__)
    latest_sig = inspect.signature(LatestComputeDispatchPort.__call__)
    assert "min_tail" not in full_sig.parameters
    assert latest_sig.parameters["min_tail"].kind is inspect.Parameter.KEYWORD_ONLY


# --------------------------------------------------------------------------- #
# 本番の具象が Port を満たす
# --------------------------------------------------------------------------- #
def test_production_collaborators_satisfy_their_ports():
    """controller が注入する本番具象は対応する Port を満たす（結線が契約どおり）。"""
    from adapter.compute import ComputeError, IndicatorComputeAdapter
    from adapter.compute import forming_bar as forming_bar_mod
    from adapter.compute.latest_dispatch import full_compute, latest_compute

    assert isinstance(IndicatorComputeAdapter(), IndicatorComputePort)
    assert isinstance(forming_bar_mod, FormingBarPort)
    assert isinstance(full_compute, ComputeDispatchPort)
    assert isinstance(latest_compute, LatestComputeDispatchPort)
    assert isinstance(ComputeError("validation", "msg"), ComputeErrorPort)


def _params_of(func, *, drop_self: bool = False) -> "list[tuple]":
    """(名前, 種別, 既定の有無) の列。注釈の書式差（PEP 563 の文字列化）には依存させない。"""
    items = list(inspect.signature(func).parameters.values())
    if drop_self:
        items = items[1:]
    return [(p.name, p.kind, p.default is not inspect.Parameter.empty) for p in items]


def test_port_signatures_match_the_measured_concretes_exactly():
    """Port の宣言シグネチャが本番具象の実シグネチャと厳密一致する（推測で増減させていない）。

    ``__call__`` だけを宣言する Protocol の ``isinstance`` は「callable であること」しか見ないため、
    ディスパッチ 2 種の契約は本テストのシグネチャ比較でのみ実証できる。
    """
    from adapter.compute import IndicatorComputeAdapter
    from adapter.compute import forming_bar as forming_bar_mod
    from adapter.compute.latest_dispatch import full_compute, latest_compute

    assert _params_of(full_compute) == _params_of(ComputeDispatchPort.__call__, drop_self=True)
    assert _params_of(latest_compute) == _params_of(
        LatestComputeDispatchPort.__call__, drop_self=True
    )
    assert _params_of(IndicatorComputeAdapter.compute, drop_self=True) == _params_of(
        IndicatorComputePort.compute, drop_self=True
    )
    for name in ("resolve_now_unix", "apply_forming_bar"):
        assert _params_of(getattr(forming_bar_mod, name)) == _params_of(
            getattr(FormingBarPort, name), drop_self=True
        ), name


def test_controller_injects_exactly_the_contracted_collaborators():
    """controller の注入面が本ガードの対象と一致する（未検証の第 6 の協調子が増えていない）。"""
    from adapter.controller import compute_controller as cc

    src = inspect.getsource(cc.handle_compute)
    for kw in ("compute_adapter=", "forming_bar=", "full_compute=",
               "latest_compute=", "compute_error="):
        assert kw in src


# --------------------------------------------------------------------------- #
# テスト fake も Port 準拠（ISSUE-177 と同方針）
# --------------------------------------------------------------------------- #
def test_unit_test_fakes_conform_to_the_same_ports():
    """usecase 単体テストの fake 協調子も Port を満たす（fake を緩めず Port 準拠へ拡充する）。"""
    from test_usecase_compute_indicators import _kw

    kw = _kw()
    assert isinstance(kw["dataset_port"], DatasetPort)
    assert isinstance(kw["compute_adapter"], IndicatorComputePort)
    assert isinstance(kw["forming_bar"], FormingBarPort)
    assert isinstance(kw["full_compute"], ComputeDispatchPort)
    assert isinstance(kw["latest_compute"], LatestComputeDispatchPort)
    assert isinstance(kw["compute_error"]("validation", "msg"), ComputeErrorPort)


def test_compute_ports_module_has_no_outer_layer_imports():
    """Port 定義は adapter / marketdata を import しない（Dependency Rule・ISSUE-183 のガードと同旨）。"""
    import usecase.compute_ports as cp

    src = inspect.getsource(cp)
    for line in src.splitlines():
        stripped = line.lstrip()
        if stripped.startswith(("import ", "from ")):
            assert not stripped.split()[1].startswith(("adapter", "marketdata")), line
