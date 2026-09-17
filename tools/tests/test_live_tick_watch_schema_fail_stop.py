"""起動時の列形照合と周期中の致命例外の通過口（ISSUE-511 段階 3 の段階 4・V-4）。

用語（初出定義）:
    列形の食い違い
        ＝ 既存 M1 CSV の先頭行（ヘッダ）に spread 列があるかと、台帳の宣言
          （dataset_registry の記述子欄 spread_point_snapshot）の有無が一致しないこと。
          判定の規則そのものは ``tick_m1`` が持ち、本検定はその公開面
          :func:`tick_m1.check_series_schema` を常駐が起動時に通ることを固定する。
    格下げ
        ＝ 内側が Fail-Stop として送出した例外を、外側の包括 ``except`` が WARNING として
          記録し次周期へ進むこと。格下げされると同じ失敗を繰り返したまま常駐が回り続ける。

本検定が固定するもの:
  R-11 起動時に列形が食い違うと、``data_dir`` の下へ 1 バイトも書かずに止まる
       （ロックファイルも作らない＝先行の書き手へ SIGTERM を送らないまま止まる）。
  R-12 そのとき非 0 で終了する（格下げされて WARNING のまま続行しない）。
  R-13 偽陽性が無い（既存 CSV が無い・列形が一致するときは起動が止まらない）。
  F-1  周期中の食い違いが、致命の型を渡した呼び方では格下げされない（ポーリング常駐・ストリーミング常駐）。
  F-2  致命の型を渡さない既定の呼び方では、従来どおり一過性例外を握って継続する＝共有中立核の挙動不変。
  CX-D 計算量（Test Spy・発行 − 使用 = 0・規模 2 点・回数は期待値に焼き込まない）。

書込はすべて ``tmp_path`` の下（常駐・サーバは起動しない。ネットワークは叩かない）。
構造: Arrange-Act-Assert（AAA）。
"""
from __future__ import annotations

import datetime as dt
from pathlib import Path

import pytest

from common import watch_loop
from marketdata import csv_schema, dukascopy_source, tick_m1
from tools import live_tick_watch as ltw

#: 実体のループ（テストが差し替えたあとも、周期数を有界にして呼ぶために掴んでおく）。
_REAL_RUN_WATCH = watch_loop.run_watch

#: 固定の「現在時刻」（テスト決定性・実時計を読まない）。
_NOW = dt.datetime(2026, 9, 17, 12, 0, 0)

#: 列形 2 種（順序は台帳側の規則 :func:`marketdata.csv_schema.header_for` から導く）。
_PLAIN = csv_schema.header_for(["open", "high", "low", "close", "volume", "up", "dn"])
_WITH_SPREAD = csv_schema.header_for(
    ["open", "high", "low", "close", "volume", "up", "dn", csv_schema.SPREAD_COLUMN]
)


def _spy(monkeypatch, module, name: str) -> "list[tuple]":
    """``module.name`` を包み、発行ごとに引数を記録する Test Spy（モジュール属性の継ぎ目）。

    属性が無ければ AttributeError（継ぎ目の不在がそのまま失敗として見える）。
    """
    real = getattr(module, name)
    calls: "list[tuple]" = []

    def recorded(*args, **kwargs):
        calls.append(args)
        return real(*args, **kwargs)

    monkeypatch.setattr(module, name, recorded)
    return calls


def _tree(root: Path) -> "dict[str, bytes]":
    """``root`` 配下の全ファイルの（相対パス, 中身）（書込の有無を数える観測点）。"""
    return {
        str(p.relative_to(root)): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()
    }


def _write_m1(data_dir: Path, columns: "list[str]") -> Path:
    """常駐が書く系列の置き場へ、``columns`` の列形だけを持つ既存 CSV を置く。"""
    path = tick_m1.m1_csv_path(ref=ltw.REF, data_dir=data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(",".join(columns) + "\n", encoding="utf-8")
    return path


def _stub_modes(monkeypatch) -> "dict[str, list]":
    """起動後に走る 3 つの口（追い付き・1 周期・ストリーミング）を差し替え、呼ばれた回数を記録する。"""
    seen: "dict[str, list]" = {"catch_up": [], "update_once": [], "stream_loop": []}
    monkeypatch.setattr(ltw, "catch_up", lambda *a, **k: seen["catch_up"].append(a) or 0)
    monkeypatch.setattr(ltw, "update_once", lambda *a, **k: seen["update_once"].append(a))
    monkeypatch.setattr(ltw, "stream_loop", lambda *a, **k: seen["stream_loop"].append(a))
    monkeypatch.setattr(ltw, "_utc_now", lambda: _NOW)
    return seen


def _bounded_loop(monkeypatch, cycles: int) -> None:
    """常駐のポーリングを ``cycles`` 周期で終わらせる（待機は入れない・引数はそのまま通す）。"""

    def bounded(update_fn, **kwargs):
        return _REAL_RUN_WATCH(update_fn, sleep_fn=lambda _s: None, stop_after=cycles, **kwargs)

    monkeypatch.setattr(watch_loop, "run_watch", bounded)


_MODES = [
    pytest.param(["--once"], "update_once", id="once"),
    pytest.param(["--stream"], "stream_loop", id="stream"),
]


# =====================================================================
# R-11 / R-12 起動時の Fail-Stop（何も書かない・非 0 で終了）
# =====================================================================
@pytest.mark.parametrize(("extra", "mode"), _MODES)
def test_a_mismatching_column_form_writes_nothing_at_start(monkeypatch, tmp_path, extra, mode):
    """R-11: 宣言と食い違う列形の既存 CSV があるとき、起動は 1 バイトも書かずに止まる。

    ロックファイルも作らない＝先行の書き手（--takeover）を停めないまま止まる。
    """
    # Arrange
    _write_m1(tmp_path, _WITH_SPREAD)   # 台帳は spread を宣言していない系列（食い違い）。
    seen = _stub_modes(monkeypatch)
    before = _tree(tmp_path)

    # Act
    ltw.main(["--data-dir", str(tmp_path), "--quiet", *extra])

    # Assert
    assert _tree(tmp_path) == before
    assert seen["catch_up"] == []
    assert seen[mode] == []


@pytest.mark.parametrize(("extra", "mode"), _MODES)
def test_a_mismatching_column_form_exits_non_zero(monkeypatch, tmp_path, extra, mode):
    """R-12: 食い違いは WARNING で続行せず、非 0 の終了コードで止まる。"""
    # Arrange
    _write_m1(tmp_path, _WITH_SPREAD)
    _stub_modes(monkeypatch)

    # Act
    rc = ltw.main(["--data-dir", str(tmp_path), "--quiet", *extra])

    # Assert
    assert rc != 0


# =====================================================================
# R-13 偽陽性が無い
# =====================================================================
@pytest.mark.parametrize(
    "prepare",
    [pytest.param(lambda d: None, id="no_file"),
     pytest.param(lambda d: _write_m1(d, _PLAIN), id="matching_header")],
)
def test_a_matching_column_form_does_not_stop_the_start(monkeypatch, tmp_path, prepare):
    """R-13: 既存 CSV が無い／列形が宣言と一致するときは、起動が止まらず従来どおり 1 周期走る。"""
    # Arrange
    prepare(tmp_path)
    seen = _stub_modes(monkeypatch)

    # Act
    rc = ltw.main(["--data-dir", str(tmp_path), "--quiet", "--once"])

    # Assert
    assert rc == 0
    assert len(seen["catch_up"]) == 1
    assert len(seen["update_once"]) == 1


# =====================================================================
# F-1 周期中の食い違いは格下げされない（常駐 2 経路）
# =====================================================================
def test_the_polling_loop_stops_at_the_cycle_that_hits_the_mismatch(monkeypatch, tmp_path):
    """ポーリング常駐: 周期中の食い違いは次周期へ進まず、非 0 で終わる（同じ失敗を繰り返さない）。"""
    # Arrange: 起動時の照合は通る列形。周期の中で食い違いを検出する。
    _write_m1(tmp_path, _PLAIN)
    _stub_modes(monkeypatch)
    cycles: "list[tuple]" = []

    def _raise(*args, **kwargs):
        cycles.append(args)
        raise tick_m1.SpreadSchemaMismatch("周期の中で検出した列形の食い違い")

    monkeypatch.setattr(ltw, "update_once", _raise)
    _bounded_loop(monkeypatch, 3)

    # Act
    rc = ltw.main(["--data-dir", str(tmp_path), "--quiet"])

    # Assert: 握り潰す実装なら 3 周期走って rc == 0 になる。
    assert len(cycles) == 1
    assert rc != 0


def test_the_stream_loop_does_not_swallow_the_mismatch(monkeypatch, tmp_path):
    """ストリーミング常駐: 分確定の連鎖が出した食い違いを、包括 except が WARNING へ格下げしない。"""
    # Arrange
    clock = {"n": 0}

    def _now() -> dt.datetime:
        clock["n"] += 1
        return _NOW + dt.timedelta(minutes=2 * clock["n"])

    chained: "list[int]" = []

    def _chain(now, data_dir, full_start) -> None:
        chained.append(len(chained))
        if len(chained) >= 2:            # 1 回目は起動直後の追い付き、2 回目が周期の中。
            raise tick_m1.SpreadSchemaMismatch("周期の中で検出した列形の食い違い")

    sleeps: "list[float]" = []

    def _sleep(seconds: float) -> None:
        sleeps.append(seconds)
        if len(sleeps) > 20:             # 安全弁: 格下げする実装でも試験が終わる。
            raise KeyboardInterrupt

    monkeypatch.setattr(ltw, "_utc_now", _now)
    monkeypatch.setattr(ltw, "refresh_day_parquet", lambda day, data_dir, **k: 0)
    monkeypatch.setattr(ltw, "_load_day_frame", lambda day, data_dir: None)
    monkeypatch.setattr(ltw, "_chain_m1_rollup", _chain)
    monkeypatch.setattr(dukascopy_source, "fetch_ticks_since", lambda *a, **k: [])
    monkeypatch.setattr(ltw.time, "sleep", _sleep)

    # Act
    with pytest.raises(BaseException) as caught:  # noqa: PT011 — 型は Assert で名指しする
        ltw.stream_loop(tmp_path, interval=0.0, full_start=dt.date(2026, 9, 1))

    # Assert: 格下げする実装では安全弁の KeyboardInterrupt が出る（型で見分ける）。
    assert caught.type is tick_m1.SpreadSchemaMismatch
    assert len(chained) == 2


# =====================================================================
# F-2 共有中立核の既定は挙動不変（もう 1 本の常駐 export_jp225_m1 --watch が使う呼び方）
# =====================================================================
def test_the_loop_reraises_an_error_named_as_fatal() -> None:
    """致命として渡した型は握らず、その周期で送出したまま止まる。"""
    # Arrange
    attempts: "list[int]" = []

    def _update() -> None:
        attempts.append(len(attempts))
        raise tick_m1.SpreadSchemaMismatch("列形の食い違い")

    # Act
    with pytest.raises(BaseException) as caught:  # noqa: PT011 — 型は Assert で名指しする
        _REAL_RUN_WATCH(_update, interval=1, sleep_fn=lambda _s: None, stop_after=5,
                        fatal=(tick_m1.SpreadSchemaMismatch,))

    # Assert
    assert caught.type is tick_m1.SpreadSchemaMismatch
    assert attempts == [0]


def test_a_transient_error_is_still_swallowed_when_a_fatal_type_is_given() -> None:
    """致命の型を渡しても、名指ししていない一過性例外は従来どおり握って次周期へ進む。"""
    # Arrange
    attempts: "list[int]" = []

    def _flaky() -> None:
        attempts.append(len(attempts))
        raise RuntimeError("一過性障害")

    # Act
    rc = _REAL_RUN_WATCH(_flaky, interval=1, sleep_fn=lambda _s: None, stop_after=3,
                         fatal=(tick_m1.SpreadSchemaMismatch,))

    # Assert
    assert rc == 0
    assert attempts == [0, 1, 2]


def test_the_default_loop_swallows_the_same_error_as_before() -> None:
    """致命の型を渡さない既定の呼び方では、同じ型でも従来どおり握って継続する＝挙動不変。

    共有中立核を使うもう 1 本の常駐（indigators 側の export_jp225_m1 の --watch）は
    この呼び方のままであり、本段でその挙動を変えない。
    """
    # Arrange
    attempts: "list[int]" = []

    def _update() -> None:
        attempts.append(len(attempts))
        raise tick_m1.SpreadSchemaMismatch("列形の食い違い")

    # Act
    rc = _REAL_RUN_WATCH(_update, interval=1, sleep_fn=lambda _s: None, stop_after=3)

    # Assert
    assert rc == 0
    assert attempts == [0, 1, 2]


# =====================================================================
# CX-D 計算量（継ぎ目 tick_m1._existing_csv_header・発行 − 使用 = 0・周期数 2 点）
# =====================================================================
def _reads_for_cycles(monkeypatch, tmp_path: Path, cycles: int) -> "tuple[int, int, int, int]":
    """``cycles`` 周期の常駐を回し、（終了コード, ヘッダ読取, 照合, 消化周期）を返す。"""
    data_dir = tmp_path / f"cycles{cycles}"
    _write_m1(data_dir, _PLAIN)
    seen = _stub_modes(monkeypatch)
    reads = _spy(monkeypatch, tick_m1, "_existing_csv_header")
    checks = _spy(monkeypatch, tick_m1, "check_series_schema")
    _bounded_loop(monkeypatch, cycles)

    rc = ltw.main(["--data-dir", str(data_dir), "--quiet"])
    return rc, len(reads), len(checks), len(seen["update_once"])


def test_cxd_header_reads_do_not_grow_with_the_number_of_cycles(monkeypatch, tmp_path) -> None:
    """CX-D: 起動時照合のヘッダ読取は、周期数 2 と 20 で等しい（周期ごとに照合し直さない）。

    - 発行（``tick_m1._existing_csv_header`` の呼出）− 使用（照合が出した判定の数）= 0。
    - 2 点（周期 2 / 20）で表明するのは「発行が周期数では増えない」というオーダーであり、
      回数そのもの（N 回読むこと）は期待値に焼き込まない（期待値は観測した判定数から導く）。
    """
    # Arrange / Act
    small_rc, small_reads, small_checks, small_cycles = _reads_for_cycles(monkeypatch, tmp_path, 2)
    large_rc, large_reads, large_checks, large_cycles = _reads_for_cycles(monkeypatch, tmp_path, 20)

    # Assert
    assert (small_rc, large_rc) == (0, 0)
    assert (small_cycles, large_cycles) == (2, 20)       # 空振り防止（周期が実際に回った）
    assert small_checks > 0                              # 空振り防止（照合がヘッダを読んでいる）
    assert small_reads - small_checks == 0, f"周期 2: 読取 {small_reads} − 照合 {small_checks} ≠ 0"
    assert large_reads - large_checks == 0, f"周期 20: 読取 {large_reads} − 照合 {large_checks} ≠ 0"
    assert small_reads == large_reads, (
        f"ヘッダ読取が周期 2 の {small_reads} から周期 20 で {large_reads} へ増えました"
        "（周期ごとに列形を照合し直しています）。"
    )
