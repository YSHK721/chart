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
    :class:`tick_m1.SpreadSchemaMismatch` の送出点はリポジトリに 1 つだけで
    （``marketdata/tick_m1.py`` の ``tick_m1._assert_spread_schema``）、そこへ到達するのは
    ``tick_m1._checked_series`` を通る口に限られる。**MT5 の日中追記もその 1 つである**
    （ISSUE-511 段階 3 の段階 5 で ``marketdata/mt5_ticks/m1_chain.py`` の畳みが
    ``tick_m1.fold_ticks_for`` 経由になり、系列（ref）を渡さない唯一の畳み口が無くなった）。
    日次再構築が列構成の食い違いで送出するのは
    :class:`~marketdata.mt5_ticks.port.Mt5SupplyError` であり、これは既に捕捉集合に在る。

    よって本検定が固定するのは次の 2 つである:
      - 起動時の照合で食い違いを検出し、何も書かずに fail-stop で終わること（R-11〜R-13）。
      - **現行の周期経路が実際にこの型を送出し**、格下げされずに fail-stop で終わること（F-1）。

    F-1 の格上げの実測（2026-09-17・合成・本コンテナ・2 周期・段階 5 の実装で測り直した）:
    起動時は既存 CSV が無く照合は素通し → 1 周期目が宣言どおり（spread 無し）の CSV を 2 行で
    作る → 周期の継ぎ目で台帳が spread を宣言する（段階 7 の投入と同じ形）→ 2 周期目の日中追記が
    照合に掛かり ``tick_m1.SpreadSchemaMismatch``・exit 3・**2 周期目の追記 0 行**・traceback なし。

    段階 5 **前**のツリー（HEAD＝9f598012 を scratchpad へ展開）で同じ 2 周期シナリオを測り直した
    （2026-09-17・同じ合成データ・本コンテナ）。迂回していた頃の振る舞いは**既存 CSV の列形で
    2 つに分かれ**、この 2 周期シナリオは「落ちない」方だった:
      - 既存 CSV が spread 列を持たない／存在しないとき（＝本試験のシナリオ）: **止まらない**。
        台帳が spread を宣言しても、日中追記は宣言と違う列形（spread 無し）で書き続ける。実測は
        exit 0・例外なし・2 周期目も 2 行追記（CSV は 2 → 4 行）＝**静かな乖離**。
      - 既存 CSV が spread 列を持つとき: 畳みが spread 列を作れないため ISSUE-455 のヘッダ不一致
        ``ValueError``（捕捉集合の外）で書込 0 バイト。
    よって段階 5 で変わったのは「落ち方」ではなく、**静かに乖離していた側が止まるようになったこと**
    である（型の格上げだけを見ると、この価値が読めない）。

書込はすべて ``tmp_path`` の下（常駐・サーバは起動しない。ネットワークは叩かない）。
構造: Arrange-Act-Assert（AAA）。
"""
from __future__ import annotations

import dataclasses
import datetime as dt
from pathlib import Path
from typing import NamedTuple

import pytest

from marketdata import csv_schema, tick_m1
from marketdata import symbol_spec_snapshot as sss
from marketdata.dataset_registry import REGISTRY
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

#: 宣言する組（実在するスナップショット）。値は解決まで至らない（照合が先に止める）。
_PAIR = (sss.OANDA_JAPAN_MT5_LIVE, "JP225")


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
# F-1 周期の中の致命型は格下げされない（**現行経路が実際に送出する**）
# =====================================================================
def _observed(data_dir: Path) -> "tuple[str | None, int]":
    """常駐が書いた M1 CSV の（先頭行, データ行数）。不在は ``(None, 0)``。"""
    path = tick_m1.m1_csv_path(ref=watch.DEFAULT_REF, data_dir=data_dir)
    lines = path.read_text(encoding="utf-8").splitlines() if path.is_file() else []
    return (lines[0] if lines else None, max(len(lines) - 1, 0))


def test_a_fatal_mismatch_from_the_real_cycle_is_not_downgraded(tmp_path, secret, monkeypatch, capsys):
    """F-1: 周期の中の**日中追記**が送出した列形の食い違いで、次周期へ進まず fail-stop で終わる。

    段階 7（台帳へ spread を宣言する）を常駐の稼働中に行った形をそのまま再現する: 起動時は既存
    CSV が無く照合は素通し → 1 周期目が宣言どおり（spread 無し）の CSV を作る → 周期の継ぎ目で
    台帳が宣言する → 2 周期目の日中追記が照合に掛かる。差し替えるのは**台帳の宣言と時計だけ**で
    あり、周期の経路そのものは本番と同じである（かつてここは ``watch.build_cycle`` を丸ごと
    差し替えて「通過口の存在」だけを固定しており、現行経路が送出するかは測っていなかった）。

    型集合から漏れていれば、常駐はトレースバックを吐いて exit 1 で落ちる（運用者には未知の
    クラッシュに見える）。格下げする実装なら次の周期へ進み、待ちも発行される。
    """
    # Arrange
    source = fakes.CountingTickSource(_tape(_START, minutes=30))
    clock = fakes.FixedClock(_NOW)
    settings = watch.settings_from(watch.build_parser().parse_args(
        ["--data-dir", str(tmp_path), "--from", _FROM]
    ))
    seen: "list[tuple[str | None, int]]" = []

    def between_cycles(_seconds):
        """周期の継ぎ目: 新しい分が閉じるまで時計を進め、台帳が spread を宣言する。"""
        seen.append(_observed(tmp_path))
        clock.advance(minutes=2)
        monkeypatch.setitem(REGISTRY, watch.DEFAULT_REF, dataclasses.replace(
            REGISTRY[watch.DEFAULT_REF], spread_point_snapshot=_PAIR
        ))

    # Act
    code = watch.run(settings, source=source, clock=clock, cycles=2, sleep=between_cycles)

    # Assert
    assert code == watch.EXIT_FAIL_STOP
    assert len(seen) == 1, f"周期の継ぎ目が {len(seen)} 回でした（2 周期目で止まっていません）"
    assert seen[0] == (",".join(_PLAIN), 2)      # 空振り防止（1 周期目は宣言どおりに書いた）
    assert _observed(tmp_path) == seen[0]        # 2 周期目は 1 バイトも書いていない
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
