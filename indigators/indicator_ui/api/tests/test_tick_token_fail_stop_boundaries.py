"""台帳の記入漏れ（Fail-Stop）が **本番の主経路で握り潰されない** ことを固定する（ISSUE-512 段階 1）。

台帳に枝名を書き忘れたティック ref は :func:`marketdata.dataset_registry.tick_tree_token` で
止まる設計である。ところが呼び出し側の境界は、素材（tick parquet）の torn-read / IO 失敗を
**握って継続する** ために包括的な ``except Exception`` を置いている。記入漏れはその網に掛かり、
WARNING 1 行と「注入しない素通し」へ化ける。落ちないぶん出力は形式上正しく、状態検証では
原理的に検出できない（ISSUE-450 と同型）。

本検定が固定するもの:
  1. 記入漏れ専用の例外型が在り、``ValueError`` の派生である（既存の捕捉契約を壊さない）。
  2. ``/compute`` の入口（``apply_forming_bar``）で記入漏れが外へ出る。
  3. ``/live_ticks`` の入口（``handle_live_tick_tails``）で記入漏れが外へ出る。
  4. **それ以外の失敗の扱いは変わらない**。素材読込の失敗は従来どおり素通し／当該グループ落とし
     であり、注入バーの破損（time 欄が非数値のときの ValueError）も従来どおり握られる。
     4 は 2・3 を ``except ValueError: raise`` で済ませる実装を落とすためにある（その書き方は
     素材の破損と台帳の記入漏れを同じ網で捕らえてしまい、区別という目的を達しない）。

data/: 実データを読まない（台帳への一時記述子・合成 DataFrame・注入した偽物のみ）。
構造: Arrange-Act-Assert（AAA）。
"""
from __future__ import annotations

import json
import logging

import pandas as pd
import pytest

from marketdata import dataset_registry, tf_meta
from marketdata.dataset_registry import REGISTRY, DatasetDescriptor, TickTokenMissing

from adapter.compute import forming_bar as fb
from adapter.compute import live_tick_tails as ltt
from adapter.controller import live_tick_tails_controller as ctl

#: 台帳へ一時的に載せる「tick=True だがトークン未記入」の ref（記入漏れの再現）。
_OMITTED = "zz_ledger_omission_ref"
_NOW = 1_787_887_980
_TF = "5m"


@pytest.fixture(autouse=True)
def _clear_cache():
    fb.clear_forming_cache()
    yield
    fb.clear_forming_cache()


@pytest.fixture
def _ledger_omission(monkeypatch, tmp_path):
    """台帳に「tick=True・tick_token 未記入」の ref を 1 件だけ足す（記入漏れの実物）。

    偽の例外を注入するのではなく台帳そのものを記入漏れの状態にするため、窓口から境界までの
    結線ごと検査できる（境界の except 節だけを見るより広い）。
    """
    monkeypatch.setitem(
        REGISTRY, _OMITTED,
        DatasetDescriptor(path=tmp_path / "omitted.csv", symbol="XXX", tick=True),
    )
    monkeypatch.setattr(tf_meta, "TICK_REFS", frozenset(set(tf_meta.TICK_REFS) | {_OMITTED}))
    return _OMITTED


def _one_row_window(unix_seconds: int) -> "pd.DataFrame":
    """確定バー 1 本だけの窓（date-index・OHLCV）。"""
    return pd.DataFrame(
        {"open": [1.0], "high": [2.0], "low": [0.5], "close": [1.5], "volume": [3.0]},
        index=pd.DatetimeIndex([pd.Timestamp(unix_seconds, unit="s")], name="date"),
    )


# --------------------------------------------------------------------------- #
# 1. 記入漏れ専用の例外型
# --------------------------------------------------------------------------- #
def test_the_ledger_omission_has_a_dedicated_exception_type(_ledger_omission):
    """記入漏れは専用型で送出され、その型は ``ValueError`` の派生である。

    派生にするのは、記入漏れを ``ValueError`` として捕捉している既存の呼び出し側（と既存検定）
    の契約を変えないためである。専用型にするのは、境界が「素材の失敗」と「台帳の記入漏れ」を
    **型で** 区別できるようにするためである。
    """
    # Arrange / Act
    with pytest.raises(TickTokenMissing) as exc:
        tf_meta.tick_tree_token(_ledger_omission)

    # Assert
    assert issubclass(TickTokenMissing, ValueError), (
        "TickTokenMissing が ValueError の派生でない＝既存の捕捉契約を壊す"
    )
    assert _ledger_omission in str(exc.value), "どの ref の記入漏れか名指ししていない"


def test_the_dedicated_type_is_published_by_the_ledger():
    """例外型の所有者は台帳である（読取側が自前で定義しない＝単一ソース）。"""
    # Arrange / Act / Assert
    assert "TickTokenMissing" in dataset_registry.__all__


# --------------------------------------------------------------------------- #
# 2. /compute の入口
# --------------------------------------------------------------------------- #
def test_the_compute_entry_does_not_swallow_the_ledger_omission(_ledger_omission):
    """``apply_forming_bar``（``/compute`` の入口）で記入漏れが外へ出る。

    ここが握ると、台帳の窓口と adapter/compute/forming_bar.py の 2 つの算出窓口が持つ Fail-Stop
    は主経路で効果が 0 になる（早期 return で閉周期合成へ到達すらしない）。
    """
    # Arrange
    df = _one_row_window(_NOW - 3600)

    # Act / Assert
    with pytest.raises(TickTokenMissing):
        fb.apply_forming_bar(df, _ledger_omission, _TF, _NOW)


def test_the_compute_entry_still_passes_through_a_material_read_failure(monkeypatch, caplog):
    """素材読込の失敗（torn-read / IO）は従来どおり **素通し**（堅牢化を壊さない）。"""
    # Arrange
    df = _one_row_window(_NOW - 3600)

    def _torn(ref, tf, now_unix):  # noqa: ANN001, ARG001
        raise OSError("parquet torn read")

    monkeypatch.setattr(fb, "forming_bar", _torn)

    # Act
    with caplog.at_level(logging.WARNING, logger=fb.logger.name):
        out = fb.apply_forming_bar(df, "jp225_tick", _TF, _NOW)

    # Assert
    assert out is df, "素材読込の失敗で df を素通ししていない（live 経路の堅牢化が壊れた）"
    assert caplog.records, "素通しを無言で行っている（痕跡が残らない）"


def test_the_compute_entry_still_passes_through_a_plain_value_error(monkeypatch):
    """形成中バー算出に由来する **素の** ValueError は従来どおり素通しする。

    ``except ValueError: raise`` で 2 を実装すると、素材の破損が投げる ``ValueError`` まで
    貫通して ``/compute`` 全体が落ちる。区別は型でしか付かないので、その境界をここで固定する。
    """
    # Arrange
    df = _one_row_window(_NOW - 3600)

    def _corrupt(ref, tf, now_unix):  # noqa: ANN001, ARG001
        raise ValueError("素材が壊れている（台帳の記入漏れではない）")

    monkeypatch.setattr(fb, "forming_bar", _corrupt)

    # Act
    out = fb.apply_forming_bar(df, "jp225_tick", _TF, _NOW)

    # Assert
    assert out is df, "素の ValueError まで貫通させている（素材の失敗と記入漏れを混同）"


# --------------------------------------------------------------------------- #
# 3. /live_ticks の入口
# --------------------------------------------------------------------------- #
_LIVE_REF = "jp225_tick"
_FORMING_MINUTE = "2026-01-05 09:04:00"
_LAST_CONFIRMED = "2026-01-05 09:03:00"
_SPEC = [{"instanceId": "i1", "indicatorId": "profit_rsi"}]


def _unix(text: str) -> int:
    """naive UTC の日時文字列 → UNIX 秒（``.timestamp()`` は使わない）。"""
    return int(pd.Timestamp(text).value // 1_000_000_000)


_TICKS = [[_unix(_FORMING_MINUTE) * 1000 + 100, 100.0]]


class _Port:
    """確定バーだけの窓を返す偽 port。"""

    def __init__(self, frame) -> None:  # noqa: ANN001
        self._frame = frame

    def is_known(self, ref) -> bool:  # noqa: ANN001, ARG002
        return True

    def is_known_timeframe(self, tf) -> bool:  # noqa: ANN001, ARG002
        return True

    def load_dataframe(self, ref, tf):  # noqa: ANN001, ARG002
        return self._frame


@pytest.fixture
def _live_wired(monkeypatch):
    """``/live_ticks`` を、確定末尾が 1 周期古い窓（＝閉周期の穴あり）で結線する。"""
    frame = _one_row_window(_unix(_LAST_CONFIRMED) - 60)
    monkeypatch.setattr(ctl, "_dataset_port", lambda: _Port(frame))
    monkeypatch.setattr(ltt, "is_incremental", lambda *a, **k: True)
    monkeypatch.setattr(
        ctl, "latest_compute",
        lambda adapter, indicator_id, variant, window, params: [  # noqa: ANN001, ARG005
            {"name": "v", "data": [{"value": 1.0}]}
        ],
    )
    return {
        "specs": [json.dumps(_SPEC)],
        "datasetRef": [_LIVE_REF],
        "timeframe": ["1m"],
    }


def test_the_live_tails_entry_does_not_swallow_the_ledger_omission(monkeypatch, _live_wired):
    """``handle_live_tick_tails`` の窓供給で記入漏れが外へ出る（当該足を落として隠さない）。"""
    # Arrange — 閉周期合成の窓口が記入漏れで止まる状態にする（台帳が投げる型そのもの）。
    def _omitted(ref, tf, last_unix, forming_start):  # noqa: ANN001, ARG001
        raise TickTokenMissing("台帳の記入漏れ")

    monkeypatch.setattr(ctl, "closed_gap_bars", _omitted)

    # Act / Assert
    with pytest.raises(TickTokenMissing):
        ctl.handle_live_tick_tails(_live_wired, _TICKS)


def test_the_live_tails_entry_surfaces_a_real_ledger_omission(_ledger_omission, _live_wired):
    """台帳を実際に記入漏れにした ref でも、``/live_ticks`` の入口で外へ出る（結線の検査）。

    上の検定は境界へ例外を注入して**分類**を見るが、こちらは窓口から境界までの結線ごと見る。
    両方が要る: 分類だけ直して結線が切れていても、結線だけ在って分類を誤っても不合格である。
    """
    # Arrange — 記入漏れの ref を /live_ticks のクエリへ差し替える。
    query = dict(_live_wired)
    query["datasetRef"] = [_ledger_omission]

    # Act / Assert
    with pytest.raises(TickTokenMissing):
        ctl.handle_live_tick_tails(query, _TICKS)


def test_the_live_tails_entry_still_drops_only_the_group_on_a_material_failure(
    monkeypatch, _live_wired, caplog
):
    """素材由来の失敗は従来どおり **当該計算足だけ落とす**（記録あり・応答は落とさない）。"""
    # Arrange
    def _torn(ref, tf, last_unix, forming_start):  # noqa: ANN001, ARG001
        raise OSError("parquet torn read")

    monkeypatch.setattr(ctl, "closed_gap_bars", _torn)

    # Act
    with caplog.at_level(logging.ERROR, logger=ctl.logger.name):
        out = ctl.handle_live_tick_tails(_live_wired, _TICKS)

    # Assert
    assert out is None, "素材の失敗で当該グループを落とす従来挙動が変わった"
    assert caplog.records, "落としたことを記録していない（無言にしない契約）"


def test_the_live_tails_entry_still_drops_only_the_group_on_a_broken_forming_bar(
    monkeypatch, _live_wired, caplog
):
    """注入バーの破損（time 欄が非数値＝素の ValueError）は従来どおり握る。

    ``except ValueError: raise`` で記入漏れを通す実装は、注入の事前条件検査が投げるこの
    ValueError まで貫通させ、1 つの計算足の材料破損で ``/live_ticks`` の応答全体を落とす
    （境界の責務を広げてしまう）。本検定はその実装を落とす。
    """
    # Arrange
    monkeypatch.setattr(
        ctl, "closed_gap_bars",
        lambda ref, tf, last_unix, forming_start: [  # noqa: ANN001, ARG005
            {"time": "壊れている", "open": 1.0, "high": 1.0, "low": 1.0,
             "close": 1.0, "volume": 1.0}
        ],
    )

    # Act
    with caplog.at_level(logging.ERROR, logger=ctl.logger.name):
        out = ctl.handle_live_tick_tails(_live_wired, _TICKS)

    # Assert
    assert out is None, "破損バーで応答全体を落としている（素の ValueError まで貫通させた）"
    assert caplog.records, "落としたことを記録していない"
