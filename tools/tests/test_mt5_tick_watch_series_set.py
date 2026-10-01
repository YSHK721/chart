"""常駐が**台帳由来の系列の組**へ供給する（ISSUE-511 段階 8-D-2b の段 5）。

用語（初出定義）:
    系列の組
        ＝ 同じティック木（枝名 token）を読む datasetRef の全体。所有者は台帳であり、
          引く口は :func:`marketdata.dataset_registry.refs_of_tick_token` である（段 1・D-3）。
    種（seed）
        ＝ ``--ref`` が渡す 1 つの ref。段 5 で ``--ref`` は「どの木か」を指すだけに縮退し、
          **集合の決定権を失う**（例外口は作らない・依頼者裁定 2026-09-23）。運用者が台帳の
          事実を再宣言できる形が、実際に 9 日間の凍結を生んだためである。
    列の上位集合
        ＝ spread 列を宣言する系列。値列は spread の有無で変わらないため、畳みは 1 回で済む。

本検定が固定するもの（段 5 の受入条件）:
  S-1 種が 3 通り（省略・組の各 ref）でも、起動が扱う系列の組は**同じ**である。
  S-2 起動時の列形照合は**組の全 ref** に対して走る（種でない側の食い違いでも起動が止まる）。
  S-3 1 周期で**組の全系列**が publish される（上位集合だけが spread 列を持つ）。
  S-4 書込順は**上位集合が先**・最も見られている既定の系列が最後（設計 D-6 (ii)）。
  S-5 途中で書けなくなった周期は、**名前付きの例外**で「どの ref に何本書き、どの ref が
      未書込か」を載せて Fail-Stop する（素の例外で落ちない・格下げされない）。
  S-6 組の先端が揃わない（片方だけ先へ進んでいる）ときも同じ型で止まり、**最初の追記より前**に
      判断するので M1 は 1 バイトも動かない（設計 D-6 (i)(iii)）。
  CX 計算量: 系列 1 → 2 で取得・畳みの発行・スナップショット読取が**増えない**（規模 2 点）。
      回数そのものは期待値に焼き込まない。固定するのは「発行 − 使用 = 0」と増えないことである。

常駐もサーバも起動しない・ネットワークは叩かない。書込はすべて ``tmp_path`` の下である
（``--data-dir`` を必ず渡す）。構造: Arrange-Act-Assert（AAA）。
"""
from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import NamedTuple

import pytest

from marketdata import csv_schema, dataset_registry, spread_point, tick_m1
from marketdata import symbol_spec_snapshot as sss
from marketdata.mt5_ticks import fakes, m1_chain, port, server_clock
from tools import mt5_tick_watch as watch
# テープ（端末が持つティック列）の組み立ては供給常駐の既存検定が唯一の定義を持つ。ここで書き写すと
#   「MT5 はこう振る舞う」の仮定が 2 箇所へ散り、片方だけ直った瞬間に別の世界を仮定し始める。
from tools.tests.test_mt5_tick_watch import _tape

_SECRET = "mt5-series-set-secret"

#: 固定の時刻・再開点（実時計を読まない）。``_FROM`` はサーバラベル（UTC+3）である。
_START = dt.datetime(2026, 8, 25, 9, 0)
_NOW = dt.datetime(2026, 8, 25, 9, 2, tzinfo=dt.timezone.utc)
_FROM = "2026-08-25 12:00:00"

#: spread 列を持たない側の列形（順序は台帳側の規則 :func:`marketdata.csv_schema.header_for`）。
_PLAIN_HEADER = csv_schema.header_for(
    ["open", "high", "low", "close", "volume", "up", "dn"]
)


@pytest.fixture()
def secret(monkeypatch):
    monkeypatch.setenv(watch.SECRET_ENV, _SECRET)
    return _SECRET


@pytest.fixture(autouse=True)
def cleared_point_cache():
    """解決済み point の記憶を試験ごとに捨てる（F.I.R.S.T の Independent）。"""
    spread_point.forget_resolved_points()
    yield
    spread_point.forget_resolved_points()


def _ledger_refs() -> "tuple[str, ...]":
    """既定の種が指す木を読む系列の組（**綴りを書き写さず台帳から導く**期待値の素）。"""
    return dataset_registry.refs_of_tick_token(
        dataset_registry.tick_tree_token(watch.DEFAULT_REF)
    )


def _superset_ref() -> str:
    """列の上位集合（spread 列を宣言する系列）を台帳から導く。"""
    declared = [
        ref for ref in _ledger_refs()
        if dataset_registry.spread_point_snapshot_of(ref) is not None
    ]
    assert len(declared) == 1, f"spread 列を宣言する系列が {len(declared)} 件（想定は 1 件）"
    return declared[0]


def _rest_refs() -> "tuple[str, ...]":
    """上位集合ではない系列（最後に書かれる側）。"""
    return tuple(ref for ref in _ledger_refs() if ref != _superset_ref())


def _argv(tmp_path: Path, *extra: str) -> "list[str]":
    """1 周期だけ回す起動引数（再開点は明示・コールドスタート拒否に掛からないため）。"""
    return ["--data-dir", str(tmp_path), "--once", "--from", _FROM, *extra]


def _m1_path(ref: str, data_dir: Path) -> Path:
    return tick_m1.m1_csv_path(ref=ref, data_dir=data_dir)


def _m1_bytes(data_dir: Path) -> "dict[str, bytes]":
    """組の各系列の M1 CSV の中身（不在は空）。追記の有無を数える観測点。"""
    return {
        ref: (_m1_path(ref, data_dir).read_bytes()
              if _m1_path(ref, data_dir).is_file() else b"")
        for ref in _ledger_refs()
    }


def _data_rows(path: Path) -> int:
    if not path.is_file():
        return 0
    return max(len(path.read_text(encoding="utf-8").splitlines()) - 1, 0)


def _header_of(path: Path) -> str:
    return path.read_text(encoding="utf-8").splitlines()[0]


def _tree(root: Path) -> "dict[str, bytes]":
    """``root`` 配下の全ファイル（単一書き手ロックは台帳ではないため除く）。"""
    return {
        str(p.relative_to(root)): p.read_bytes()
        for p in sorted(root.rglob("*"))
        if p.is_file() and p.name != watch.WRITER_LOCK_FILENAME
    }


def _closed_ticks(tape, until: dt.datetime) -> int:
    """``until`` より前の分に属するティック数（＝畳みが使う行数）。

    ラベル → UTC の変換は :func:`marketdata.mt5_ticks.server_clock.to_utc_ms` が唯一源であり、
    ここで第 2 の変換規則を書かない。
    """
    limit = int(until.timestamp() * 1000)
    return len([row for row in tape if server_clock.to_utc_ms(row[0]) < limit])


# =====================================================================
# S-1 種が 3 通りでも系列の組は同じ
# =====================================================================
def _refs_checked_at_start(monkeypatch, tmp_path: Path, *extra: str) -> "tuple[int, tuple]":
    """起動が列形を照合した ref を順に記録する（照合は書く前・読むだけ）。"""
    checked: "list[str]" = []
    real = tick_m1.check_series_schema

    def spy(ref, **kwargs):
        checked.append(ref)
        return real(ref, **kwargs)

    monkeypatch.setattr(tick_m1, "check_series_schema", spy)
    code = watch.main(
        _argv(tmp_path, "--no-publish", *extra),
        source=fakes.CountingTickSource(_tape(_START, minutes=2)),
        clock=fakes.FixedClock(_NOW),
    )
    return code, tuple(checked)


@pytest.mark.parametrize("seed", [None, *_ledger_refs()], ids=lambda s: s or "omitted")
def test_every_seed_resolves_to_the_same_series_set(seed, tmp_path, secret, monkeypatch):
    """S-1: ``--ref`` 省略・組のどの ref を渡しても、扱う系列の組は台帳のタプルそのものである。

    ``--ref`` が集合を決めていれば、種ごとに違う組（1 件だけ・種だけ）になってここで落ちる。
    """
    # Arrange（期待値は台帳から導く＝綴りを書き写さない）
    expected = _ledger_refs()
    assert len(expected) >= 2, "同じ木を読む ref が 2 件未満（この検定が空振りしている）"
    extra = () if seed is None else ("--ref", seed)

    # Act
    code, checked = _refs_checked_at_start(monkeypatch, tmp_path, *extra)

    # Assert
    assert code == watch.EXIT_OK
    assert checked == expected


# =====================================================================
# S-2 起動時の列形照合は組の全 ref に対して走る
# =====================================================================
def test_a_mismatch_on_a_series_other_than_the_seed_stops_the_start(tmp_path, secret):
    """S-2: 種でない系列の列形が宣言と食い違えば、起動は 1 バイトも書かずに止まる。

    照合を種だけに掛ける実装なら、食い違いは起動を素通りして周期の中で初めて落ちる
    （その頃には端末を叩き、ジャーナルへ書いた後である）。
    """
    # Arrange: 上位集合（spread を宣言する側）へ、spread 列を持たない既存 CSV を置く。
    other = _superset_ref()
    assert other != watch.DEFAULT_REF, "種と同じ系列で測っている（この検定が空振りしている）"
    path = _m1_path(other, tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(",".join(_PLAIN_HEADER) + "\n", encoding="utf-8")
    source = fakes.CountingTickSource(_tape(_START, minutes=2))
    before = _tree(tmp_path)

    # Act
    code = watch.main(_argv(tmp_path), source=source, clock=fakes.FixedClock(_NOW))

    # Assert
    assert code == watch.EXIT_FAIL_STOP
    assert (source.token_probes, source.cycle_fetches) == (0, 0)
    assert _tree(tmp_path) == before


# =====================================================================
# S-3 1 周期で組の全系列が publish される
# =====================================================================
def test_one_cycle_publishes_every_series_of_the_set(tmp_path, secret):
    """S-3: 閉じた分が組の**すべての**系列へ出る（1 系列しか書かない実装ならここで落ちる）。"""
    # Arrange
    source = fakes.FakeTickSource(_tape(_START, minutes=3))

    # Act
    code = watch.main(_argv(tmp_path), source=source, clock=fakes.FixedClock(_NOW))

    # Assert
    assert code == watch.EXIT_OK
    rows = {ref: _data_rows(_m1_path(ref, tmp_path)) for ref in _ledger_refs()}
    assert min(rows.values()) > 0, f"書かれていない系列があります: {rows}"
    assert len(set(rows.values())) == 1, f"系列ごとに本数が違います: {rows}"
    # 上位集合だけが spread 列を持つ（射影が配られている）。
    assert csv_schema.SPREAD_COLUMN in _header_of(_m1_path(_superset_ref(), tmp_path))
    for ref in _rest_refs():
        assert csv_schema.SPREAD_COLUMN not in _header_of(_m1_path(ref, tmp_path))


# =====================================================================
# S-4 書込順（上位集合が先・最も見られている系列が最後）
# =====================================================================
def test_the_superset_series_is_written_before_the_most_watched_one(tmp_path, secret, monkeypatch):
    """S-4: 追記の発行順は上位集合が先である（途中で死ぬと遅れるのが観測される側になる）。

    2 ファイルの原子的書込はできないため、どちらが遅れるかは選べる唯一の性質である。
    """
    # Arrange
    order: "list[Path]" = []
    real = tick_m1.append_m1_rows

    def spy(m1, path):
        order.append(Path(path))
        return real(m1, path)

    monkeypatch.setattr(tick_m1, "append_m1_rows", spy)

    # Act
    code = watch.main(
        _argv(tmp_path),
        source=fakes.FakeTickSource(_tape(_START, minutes=3)),
        clock=fakes.FixedClock(_NOW),
    )

    # Assert
    expected = [_m1_path(ref, tmp_path) for ref in (_superset_ref(), *_rest_refs())]
    assert code == watch.EXIT_OK
    assert order == expected


# =====================================================================
# S-5 部分書込は名前付きの例外で Fail-Stop
# =====================================================================
def test_a_write_that_fails_halfway_stops_with_a_named_error(tmp_path, secret, monkeypatch, capsys):
    """S-5: 上位集合を書いた後に失敗すると、どの ref に何本書けたかを載せて止まる。

    素の例外で落ちれば常駐はトレースバックを吐いて exit 1 になり、運用者には未知のクラッシュに
    見える（どちらの系列が遅れているのかも分からない）。
    """
    # Arrange: 最後に書かれる側（最も見られている系列）だけを故障させる。
    late = _rest_refs()[0]
    real = tick_m1.append_m1_rows

    def flaky(m1, path):
        if Path(path) == _m1_path(late, tmp_path):
            raise OSError("書込に失敗しました（試験が起こした故障）")
        return real(m1, path)

    monkeypatch.setattr(tick_m1, "append_m1_rows", flaky)

    # Act
    code = watch.main(
        _argv(tmp_path),
        source=fakes.FakeTickSource(_tape(_START, minutes=3)),
        clock=fakes.FixedClock(_NOW),
    )

    # Assert
    err = capsys.readouterr().err
    wrote = _data_rows(_m1_path(_superset_ref(), tmp_path))
    assert code == watch.EXIT_FAIL_STOP
    assert "Traceback" not in err
    assert wrote > 0, "上位集合も書けていない（部分書込の状況を作れていない）"
    # 綴りの包含では測らない（既定の系列名は上位集合の名前の接頭辞である）。案内の要素
    #   「どの ref に何本書いたか」「どの ref が未書込か」を 1 つずつ見る。
    assert f"{_superset_ref()}={wrote} 本" in err, f"案内が書けた ref と本数を載せていません: {err}"
    assert f"未書込 {late}" in err, f"案内が未書込の ref を載せていません: {err}"


def test_a_partial_write_raises_the_named_type(tmp_path, monkeypatch):
    """S-5: 送出される型は名前付きであり、常駐の捕捉集合に載る側である。"""
    # Arrange
    late = _rest_refs()[0]
    real = tick_m1.append_m1_rows

    def flaky(m1, path):
        if Path(path) == _m1_path(late, tmp_path):
            raise OSError("書込に失敗しました（試験が起こした故障）")
        return real(m1, path)

    monkeypatch.setattr(tick_m1, "append_m1_rows", flaky)

    # Act / Assert
    with pytest.raises(port.SeriesWriteIncomplete) as exc:
        m1_chain.append_m1_for_closed_minutes_for_series(
            _tape(_START, minutes=3), refs=_ledger_refs(), data_dir=tmp_path, until=_NOW
        )
    assert late in str(exc.value)


# =====================================================================
# S-6 先端不一致は追記より前に止まる
# =====================================================================
def test_a_series_ahead_of_the_set_stops_before_any_append(tmp_path, secret, capsys):
    """S-6: 片方だけ先へ進んだ組では、追記を 1 件も発行せずに名前付きで止まる。

    先へ進んだ側は以後どの周期でも 0 本のままで、組は二度と揃わない。黙って進めると
    「片方だけ更新され続ける」状態が出力の形式上正しいまま続く。
    """
    # Arrange: 上位集合だけを 1 要素の組で 30 分先へ進める（片方だけ書けた周期の跡）。
    ahead_start = _START + dt.timedelta(minutes=30)
    m1_chain.append_m1_for_closed_minutes_for_series(
        _tape(ahead_start, minutes=2), refs=(_superset_ref(),), data_dir=tmp_path,
        until=(ahead_start + dt.timedelta(minutes=2)).replace(tzinfo=dt.timezone.utc),
    )
    before = _m1_bytes(tmp_path)
    assert before[_superset_ref()], "先端を進められていない（この検定が空振りしている）"

    # Act
    code = watch.main(
        _argv(tmp_path),
        source=fakes.FakeTickSource(_tape(_START, minutes=3)),
        clock=fakes.FixedClock(_NOW),
    )

    # Assert
    err = capsys.readouterr().err
    assert code == watch.EXIT_FAIL_STOP
    assert "Traceback" not in err
    for ref in _ledger_refs():
        assert f"{ref}=先端" in err, f"案内が {ref} の先端を載せていません: {err}"
    assert _m1_bytes(tmp_path) == before


def test_a_set_whose_tips_differ_still_supplies_when_the_new_rows_pass_both(tmp_path, secret):
    """S-6（偽陽性が無い）: 先端がずれていても、新着が**両方の先端より後**なら止まらない。

    本番の実測（2026-09-24・読取のみ）で組の先端は揃っていない（片方が前日の終端で止まっている）。
    先端差そのものを止める実装にすると、次回の起動で供給が丸ごと止まる。止めるのは「進んで
    いる側が新着より先に居て、この追記でも揃わない」ときだけである。
    """
    # Arrange: 遅れている側（上位集合）の先端を 30 分前に置き、新着はその後ろから始める。
    behind_start = _START - dt.timedelta(minutes=30)
    m1_chain.append_m1_for_closed_minutes_for_series(
        _tape(behind_start, minutes=2), refs=(_superset_ref(),), data_dir=tmp_path,
        until=(behind_start + dt.timedelta(minutes=2)).replace(tzinfo=dt.timezone.utc),
    )

    # Act
    code = watch.main(
        _argv(tmp_path),
        source=fakes.FakeTickSource(_tape(_START, minutes=3)),
        clock=fakes.FixedClock(_NOW),
    )

    # Assert: 止まらず、両系列とも同じ分まで進む（穴は残るが先端は揃う）。
    assert code == watch.EXIT_OK
    tips = {
        ref: tick_m1.last_m1_date(_m1_path(ref, tmp_path)) for ref in _ledger_refs()
    }
    assert len(set(tips.values())) == 1, f"追記後も先端が揃っていません: {tips}"


# =====================================================================
# CX 計算量（系列 1 → 2 で発行が増えない・発行 − 使用 = 0）
# =====================================================================
class _Measured(NamedTuple):
    """1 回の常駐実行の観測（終了コード・畳み・取得・スナップショット読取・書けた系列数）。"""

    code: int
    fold_rows: int
    folds: int
    fetches: int
    snapshot_reads: int
    written: int


def _measure(monkeypatch, tmp_path: Path, *, drop: "str | None") -> _Measured:
    """``drop`` を台帳から外して 1 周期回し、発行を数える（系列数だけが違う 2 点を作る）。

    継ぎ目は唯一の畳み点（``tick_m1._fold_ticks``）・供給元の取得・スナップショットの読込
    （``sss.load_snapshot``）である。公開の口の入口ではなく共通前段を数えるのは、委譲先の内部で
    2 回畳む変異が入口では 1 回に見えるためである。
    """
    data_dir = tmp_path / ("one" if drop else "two")
    data_dir.mkdir(parents=True, exist_ok=True)
    spread_point.forget_resolved_points()
    # 台帳を差し替える前に決める（差し替え後は既定の種が指す木そのものが引けない）。
    seed = _superset_ref()
    refs = _ledger_refs()
    folded: "list[int]" = []
    reads: "list[object]" = []
    real_fold = tick_m1._fold_ticks
    real_load = sss.load_snapshot
    source = fakes.CountingTickSource(_tape(_START, minutes=3))

    def fold_spy(ticks, *args, **kwargs):
        folded.append(len(ticks))
        return real_fold(ticks, *args, **kwargs)

    def load_spy(*args, **kwargs):
        reads.append(args)
        return real_load(*args, **kwargs)

    with monkeypatch.context() as patch:
        if drop is not None:
            patch.setattr(dataset_registry, "REGISTRY", {
                ref: d for ref, d in dataset_registry.REGISTRY.items() if ref != drop
            })
        patch.setattr(tick_m1, "_fold_ticks", fold_spy)
        patch.setattr(sss, "load_snapshot", load_spy)
        code = watch.main(
            ["--data-dir", str(data_dir), "--once", "--from", _FROM, "--ref", seed],
            source=source, clock=fakes.FixedClock(_NOW),
        )
    written = len([ref for ref in refs if _data_rows(_m1_path(ref, data_dir)) > 0])
    return _Measured(
        code=code, fold_rows=sum(folded), folds=len(folded),
        fetches=source.cycle_fetches, snapshot_reads=len(reads), written=written,
    )


def test_going_from_one_series_to_two_adds_no_fetch_no_fold_and_no_snapshot_read(
    tmp_path, secret, monkeypatch
):
    """CX: 系列 1 → 2 で取得・畳み・スナップショット読取のどれも増えない（規模 2 点）。

    固定するのは**無駄の不在**であり、回数そのもの（N 回）は期待値に焼き込まない。系列ごとに
    畳み直す実装・系列ごとに point を読み直す実装は、2 点の差としてここに現れる。
    """
    # Arrange / Act
    one = _measure(monkeypatch, tmp_path, drop=_rest_refs()[0])
    two = _measure(monkeypatch, tmp_path, drop=None)

    # Assert
    assert (one.code, two.code) == (watch.EXIT_OK, watch.EXIT_OK)
    assert (one.written, two.written) == (1, len(_ledger_refs()))   # 空振り防止（系列数が違う）
    assert one.folds > 0 and one.fold_rows > 0                      # 空振り防止（畳んだ）
    assert one.snapshot_reads > 0                                   # 空振り防止（point を読んだ）
    assert two.fetches == one.fetches, (
        f"取得が系列 1 の {one.fetches} から系列 2 で {two.fetches} へ増えました。"
    )
    assert two.folds == one.folds and two.fold_rows == one.fold_rows, (
        f"畳みが系列 1 の {one.folds} 回 / {one.fold_rows} 行から"
        f" 系列 2 で {two.folds} 回 / {two.fold_rows} 行へ増えました。"
    )
    assert two.snapshot_reads == one.snapshot_reads, (
        f"スナップショット読取が {one.snapshot_reads} から {two.snapshot_reads} へ増えました。"
    )
    # 発行 − 使用 = 0（畳んだ行はすべて閉じた分＝どれかの系列の出力になる）。
    used = _closed_ticks(_tape(_START, minutes=3), _NOW)
    assert two.fold_rows - used == 0, (
        f"畳みへ {two.fold_rows} 行渡したが、出力に使う閉じた分は {used} 行しかありません。"
    )
