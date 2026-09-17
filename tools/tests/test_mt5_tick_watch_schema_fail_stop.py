"""MT5 供給常駐の起動時列形照合と致命例外の通過口（ISSUE-511 段階 3 の段階 4・V-4）。

用語（初出定義）:
    列形の食い違い
        ＝ 既存 M1 CSV の先頭行（ヘッダ）が spread 列を持つかと、台帳の宣言
          （``marketdata/dataset_registry.py`` の記述子欄 ``spread_point_snapshot``）の有無が
          一致しないこと。
          判定の規則は ``tick_m1`` が持ち、本検定はその公開面
          :func:`marketdata.tick_m1.check_series_schema` を常駐が**起動時に**通ることを固定する。
    fail-stop
        ＝ 待っても直らない失敗として、再試行せず非 0 の終了コードで終わること。値は本モジュール
          既存の規約 :data:`tools.mt5_tick_watch.EXIT_FAIL_STOP` に合わせる（運用側が供給常駐
          2 本を同じ読み方で扱える）。
    格下げ
        ＝ 内側が Fail-Stop として送出した例外を、外側の ``except`` が記録だけして次周期へ
          進めること。格下げされると同じ失敗を繰り返したまま常駐が回り続ける。

射程（実測済み・憶測を仕様にしない）:
    :class:`marketdata.tick_m1.SpreadSchemaMismatch` の送出点はリポジトリに 1 つだけで
    （``marketdata/tick_m1.py`` の ``tick_m1._assert_spread_schema``）、そこへ到達するのは
    ``tick_m1._checked_series`` を通る 3 つの口（``tick_m1.check_series_schema`` /
    ``tick_m1.build_m1_from_ticks`` / ``tick_m1.append_m1_from_ticks``）に限られる。MT5 の
    日中追記は ``marketdata/mt5_ticks/m1_chain.py`` が ``tick_m1.append_m1_rows`` を直接呼ぶため
    **この 3 つを通らない**（同ファイルの docstring が ``tick_m1.append_m1_from_ticks`` を
    使わない理由を明記している）。日次再構築が列構成の食い違いで
    送出するのは :class:`~marketdata.mt5_ticks.port.Mt5SupplyError` であり、これは既に捕捉
    集合に在る。

    **現実に周期で落ちるのはヘッダ不一致の ValueError である**（射程の残り半分）: 台帳が
    spread を宣言し、既存 M1 CSV が spread 付きヘッダを持つ状態で 1 周期回すと、日中追記は
    ISSUE-455 の ``ValueError``（``tick_m1._assert_append_header_matches``）で落ちる。これは
    常駐が捕捉する型の集合の**外**であり（``SpreadSchemaMismatch`` は ``ValueError`` の派生では
    ないため、追記側の ``except ValueError`` にも本検定が固定する通過口にも掛からない）、
    traceback のまま抜けて exit 1 になる。実測（2026-09-17・合成・1 周期・本コンテナ）:
    宣言あり＋spread 付きヘッダ＝exit 1・追記 0 行／宣言なしの対照＝exit 0・3 行追記。
    既存ファイルが無い場合はこの経路に入らない（起動時照合も追記も素通しし、spread 無しの
    ヘッダで新規作成される）。

    よって本検定が固定するのは次の 2 つであって、「現行の周期経路が当該例外を送出すること」
    ではない:
      - 起動時の照合で食い違いを検出し、何も書かずに fail-stop で終わること（R-11〜R-13）。
      - 周期の中で送出されたときに格下げされない**通過口が在る**こと（F-1）。

書込はすべて ``tmp_path`` の下（常駐・サーバは起動しない。ネットワークは叩かない）。
構造: Arrange-Act-Assert（AAA）。
"""
from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import NamedTuple

import pytest

from marketdata import csv_schema, tick_m1
from marketdata.mt5_ticks import fakes
from tools import mt5_tick_watch as watch
# テープ（端末が持つティック列）の組み立ては供給常駐の既存検定が唯一の定義を持つ。ここで書き写すと
#   「MT5 はこう振る舞う」の仮定が 2 箇所へ散り、片方だけ直った瞬間に別の世界を仮定し始める。
from tools.tests.test_mt5_tick_watch import _tape

_SECRET = "mt5-schema-fail-stop-secret"

#: 固定の時刻・再開点（テスト決定性・実時計を読まない）。
_START = dt.datetime(2026, 8, 25, 9, 0)
_NOW = dt.datetime(2026, 8, 25, 9, 2, tzinfo=dt.timezone.utc)
_FROM = "2026-08-25 12:00:00"

#: 列形 2 種（順序は台帳側の規則 :func:`marketdata.csv_schema.header_for` から導く）。
#: 台帳の ``jp225_mt5`` は ``spread_point_snapshot`` を宣言していないため、spread 列を持たない
#: 側が「一致」であり、持つ側が「食い違い」である。
_PLAIN = csv_schema.header_for(["open", "high", "low", "close", "volume", "up", "dn"])
_WITH_SPREAD = csv_schema.header_for(
    ["open", "high", "low", "close", "volume", "up", "dn", csv_schema.SPREAD_COLUMN]
)


class _Measured(NamedTuple):
    """1 回の常駐実行の観測（終了コード・ヘッダ読取・照合・取得発行）。"""

    code: int
    reads: int
    checks: int
    fetches: int


@pytest.fixture()
def secret(monkeypatch):
    monkeypatch.setenv(watch.SECRET_ENV, _SECRET)
    return _SECRET


def _argv(tmp_path: Path, *extra: str) -> "list[str]":
    """1 周期だけ回す起動引数（再開点は明示・コールドスタート拒否に掛からないため）。"""
    return ["--data-dir", str(tmp_path), "--once", "--from", _FROM, *extra]


def _write_m1(data_dir: Path, columns) -> Path:
    """常駐が書く系列の置き場へ、``columns`` の列形だけを持つ既存 CSV を置く。"""
    path = tick_m1.m1_csv_path(ref=watch.DEFAULT_REF, data_dir=data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(",".join(columns) + "\n", encoding="utf-8")
    return path


def _tree(root: Path) -> "dict[str, bytes]":
    """``root`` 配下の全ファイルの（相対パス, 中身）（書込の有無を数える観測点）。"""
    return {
        str(p.relative_to(root)): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()
    }


def _measure_cycles(monkeypatch, tmp_path: Path, cycles: int) -> _Measured:
    """``cycles`` 周期の常駐を回し、ヘッダ読取と照合の発行回数を数える。

    ``--no-publish`` で回すのは、照合が見るのは spread 列の有無だけであり、追記側のヘッダ整合
    契約（ISSUE-455）と混ぜないためである（測りたいのは起動時照合の発行回数だけ）。
    """
    data_dir = tmp_path / f"cycles{cycles}"
    data_dir.mkdir(parents=True, exist_ok=True)
    _write_m1(data_dir, _PLAIN)
    reads = fakes.CallSpy(tick_m1._existing_csv_header)
    checks = fakes.CallSpy(tick_m1.check_series_schema)
    monkeypatch.setattr(tick_m1, "_existing_csv_header", reads)
    monkeypatch.setattr(tick_m1, "check_series_schema", checks)
    settings = watch.settings_from(watch.build_parser().parse_args(
        ["--data-dir", str(data_dir), "--from", _FROM, "--no-publish"]
    ))
    source = fakes.CountingTickSource(_tape(_START, minutes=30))

    code = watch.run(
        settings, source=source, clock=fakes.FixedClock(_NOW), cycles=cycles,
        sleep=lambda _s: None,
    )
    return _Measured(code, reads.count, checks.count, source.cycle_fetches)


# =====================================================================
# R-11 / R-12 起動時の Fail-Stop（何も書かない・fail-stop の終了コード）
# =====================================================================
def test_a_mismatching_column_form_writes_nothing_at_start(tmp_path, secret):
    """R-11: 宣言と食い違う列形の既存 CSV があるとき、起動は 1 バイトも書かずに止まる。

    端末も叩かない（照合は取得より前）。取得してから止めると、供給元に負荷を掛けたうえで
    どのみち書けない状態を毎回作ることになる。
    """
    # Arrange
    _write_m1(tmp_path, _WITH_SPREAD)
    source = fakes.CountingTickSource(_tape(_START, minutes=2))
    before = _tree(tmp_path)

    # Act
    watch.main(_argv(tmp_path), source=source, clock=fakes.FixedClock(_NOW))

    # Assert
    assert _tree(tmp_path) == before
    assert (source.token_probes, source.cycle_fetches) == (0, 0)


def test_a_mismatching_column_form_exits_with_the_fail_stop_code(tmp_path, secret, capsys):
    """R-12: 食い違いは既存規約の fail-stop 終了コードで止まる（未知のクラッシュに化けない）。

    型集合から漏れると常駐はトレースバックを吐いて exit 1 で落ちる。終了コードは「何が
    起きたか」を運用者へ伝える唯一の手段である。
    """
    # Arrange
    _write_m1(tmp_path, _WITH_SPREAD)

    # Act
    code = watch.main(
        _argv(tmp_path), source=fakes.FakeTickSource(_tape(_START, minutes=2)),
        clock=fakes.FixedClock(_NOW),
    )

    # Assert
    assert code == watch.EXIT_FAIL_STOP
    assert "Traceback" not in capsys.readouterr().err


# =====================================================================
# R-13 偽陽性が無い
# =====================================================================
@pytest.mark.parametrize(
    "prepare",
    [pytest.param(lambda d: None, id="no_file"),
     pytest.param(lambda d: _write_m1(d, _PLAIN), id="matching_header")],
)
def test_a_matching_column_form_does_not_stop_the_start(tmp_path, secret, prepare):
    """R-13: 既存 CSV が無い／列形が宣言と一致するときは、起動が止まらず周期が走る。"""
    # Arrange
    prepare(tmp_path)
    source = fakes.CountingTickSource(_tape(_START, minutes=2))

    # Act
    code = watch.main(
        _argv(tmp_path, "--no-publish"), source=source, clock=fakes.FixedClock(_NOW)
    )

    # Assert
    assert code == watch.EXIT_OK
    assert source.cycle_fetches == 1


# =====================================================================
# F-1 周期の中の致命型は格下げされない（通過口の存在）
# =====================================================================
def test_a_fatal_error_raised_in_a_cycle_is_not_downgraded(tmp_path, secret, monkeypatch, capsys):
    """F-1: 周期の中で送出された致命型は、次周期へ進まず fail-stop で終わる。

    現行の周期経路がこの型を送出しないことはモジュール docstring の射程に書いたとおりである。
    ここで固定するのは**通過口の存在**であり、型集合から漏れていれば常駐はトレースバックを
    吐いて exit 1 で落ちる（運用者には未知のクラッシュに見える）。
    """
    # Arrange: 起動時の照合は通る列形。周期の中で食い違いを送出する。
    _write_m1(tmp_path, _PLAIN)
    cycles: "list[int]" = []
    slept: "list[float]" = []

    def _raise(_state):
        cycles.append(len(cycles))
        raise tick_m1.SpreadSchemaMismatch("周期の中で検出した列形の食い違い")

    monkeypatch.setattr(watch, "build_cycle", lambda *a, **k: _raise)

    # Act
    code = watch.main(
        _argv(tmp_path, "--no-publish"), source=fakes.FakeTickSource(_tape(_START, minutes=2)),
        clock=fakes.FixedClock(_NOW), sleep=slept.append,
    )

    # Assert: 格下げする実装なら周期を回し続け、待ちも発行される。
    assert (code, cycles) == (watch.EXIT_FAIL_STOP, [0])
    assert slept == []
    assert "Traceback" not in capsys.readouterr().err


# =====================================================================
# CX-D 計算量（継ぎ目 tick_m1._existing_csv_header・発行 − 使用 = 0・周期数 2 点）
# =====================================================================
def test_cxd_header_reads_do_not_grow_with_the_number_of_cycles(monkeypatch, tmp_path, secret):
    """CX-D: 起動時照合のヘッダ読取は、周期数 2 と 20 で等しい（周期ごとに照合し直さない）。

    - 発行（``tick_m1._existing_csv_header`` の呼出）− 使用（照合が出した判定の数）= 0。
    - 2 点で表明するのは「発行が周期数では増えない」というオーダーであり、回数そのもの
      （N 回読むこと）は期待値に焼き込まない（期待値は観測した照合数から導く）。
    """
    # Arrange / Act
    small = _measure_cycles(monkeypatch, tmp_path, 2)
    large = _measure_cycles(monkeypatch, tmp_path, 20)

    # Assert
    assert (small.code, large.code) == (watch.EXIT_OK, watch.EXIT_OK)
    assert (small.fetches, large.fetches) == (2, 20)   # 空振り防止（周期が実際に回った）
    assert small.checks > 0                            # 空振り防止（照合が実際に発行された）
    assert small.reads - small.checks == 0, f"周期 2: 読取 {small.reads} − 照合 {small.checks} ≠ 0"
    assert large.reads - large.checks == 0, f"周期 20: 読取 {large.reads} − 照合 {large.checks} ≠ 0"
    assert small.reads == large.reads, (
        f"ヘッダ読取が周期 2 の {small.reads} から周期 20 で {large.reads} へ増えました"
        "（周期ごとに列形を照合し直しています）。"
    )
