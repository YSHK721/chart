"""系列の spread 列の有無を台帳の宣言と照合して止める（ISSUE-511 段階 3 前提 (a)・R-2/Y-2 の反転）。

用語（初出定義）:
    宣言
        ＝ 台帳記述子の ``spread_point_snapshot``（marketdata.dataset_registry）。None＝spread 列を
          持たない系列。point の値は marketdata.spread_point.spread_point_of がスナップショットから引く。
    食い違い
        ＝ 既存 M1 CSV の先頭行（ヘッダ）に spread 列があるか と 宣言の有無 が一致しないこと。
    全書換
        ＝ 既存 CSV を build の原子置換で丸ごと置き換えること（R-2/Y-2 の症状。追記のヘッダ不一致
          ValueError と末尾破損が、どちらも全構築へ落ちていた）。

本検定が固定するもの:
  6. 台帳の宣言で作った M1 は、台帳外 ref に同じ point を明示した M1 と 1 バイトも違わない。
  7. 台帳に登録済みの ref に point を明示すると拒否（ファイル bytes・mtime 不変・parquet 読込 0）。
  8. 食い違いは build / append とも拒否し全書換へ落ちない（末尾破損でも同じ）。一致なら従来どおり自己修復。
  （書き手の起動時に同じ照合で止める公開の口は ISSUE-511 段階 3 本体で足す。本段では検定しない）
  T1. SpreadSchemaMismatch は ValueError の派生ではない（追記の except ValueError＝全書換への逃げ道に
      型として捕まらない）。
  計算量 CX-1〜CX-4（Test Spy・発行 − 使用 = 0・規模 2 点・回数は期待値に焼き込まない）。

書込はすべて tmp_path（writer 呼出は必ず data_dir を渡す）。構造: Arrange-Act-Assert（AAA）。
"""
from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pandas as pd
import pytest

from marketdata import dataset_registry, quote_spread, tick_m1
from marketdata import symbol_spec_snapshot as sss
from marketdata.dataset_registry import REGISTRY, DatasetDescriptor

_TREE = "SPY225"  # tick 木の枝（テスト専用・tmp_path の中だけ）
_PAIR = (sss.OANDA_JAPAN_MT5_LIVE, "JP225")  # 宣言する組（実在するスナップショット）
_DECLARED = "zz_spread_declared"
_UNDECLARED = "zz_spread_undeclared"
_UNREGISTERED = "zz_spread_unregistered"  # 台帳に無い ref（従来どおり point 引数が効く）
_DAY0 = pd.Timestamp("2026-09-01")
_BUILD = pytest.param(tick_m1.build_m1_from_ticks, id="build")
_APPEND = pytest.param(tick_m1.append_m1_from_ticks, id="append")


def _spread_point():
    """被検査モジュール（新設）を実行時に import する（未実装で収集ごと落とさない）。"""
    return importlib.import_module("marketdata.spread_point")


@pytest.fixture(autouse=True)
def _cleared_point_cache():
    """point のキャッシュをテスト間で持ち越さない（F.I.R.S.T の Independent）。"""
    _clear_point_cache()
    yield
    _clear_point_cache()


def _clear_point_cache() -> None:
    module = sys.modules.get("marketdata.spread_point")
    if module is not None:
        module._point_size_of_snapshot.cache_clear()


def _register(monkeypatch, tmp_path: Path, ref: str, declared) -> None:
    """合成のティック ref を台帳へ一時登録する（宣言 None のときは欄を渡さない＝従来の記述子）。"""
    extra = {} if declared is None else {"spread_point_snapshot": declared}
    monkeypatch.setitem(REGISTRY, ref, DatasetDescriptor(
        path=tmp_path / f"{ref}_m1.csv", symbol="JP225", tick=True,
        price_basis="bid", vendor="dukascopy", **extra,
    ))


def _snapshot_point() -> float:
    """宣言した組の point（既存の公開経路 load_spec_fields で引く＝被検査の新関数を通さない）。"""
    return sss.load_spec_fields(*_PAIR)["point_size"]


def _day(k: int) -> pd.Timestamp:
    return _DAY0 + pd.Timedelta(days=k)


def _put_day(data_dir: Path, day: pd.Timestamp, n_minutes: int = 2) -> None:
    """``day`` の先頭 ``n_minutes`` 分 × 3 本のティック（bid < ask・幅 7.x）を tick 木へ置く。"""
    rows = [
        (day + pd.Timedelta(minutes=m, seconds=s), 66000.0 + s * 0.1, 66007.0 + s * 0.1 + m * 0.1)
        for m in range(n_minutes) for s in (5, 30, 55)
    ]
    frame = pd.DataFrame({
        "timestamp": pd.to_datetime([r[0] for r in rows]).tz_localize("UTC"),
        "bidPrice": [r[1] for r in rows],
        "askPrice": [r[2] for r in rows],
    })
    p = tick_m1.day_parquet_path(day, symbol=_TREE, data_dir=data_dir)
    p.parent.mkdir(parents=True, exist_ok=True)
    frame.to_parquet(p)


def _put_days(data_dir: Path, n_days: int) -> "list[pd.Timestamp]":
    days = [_day(k) for k in range(n_days)]
    for day in days:
        _put_day(data_dir, day)
    return days


def _run(entry, ref: str, data_dir: Path, start, end, **kw) -> Path:
    """build / append を同じ引数で呼ぶ（tick 木は _TREE・基準は bid・data_dir は必ず tmp）。"""
    return entry(start, end, symbol=_TREE, ref=ref, data_dir=data_dir, price_basis="bid", **kw)


def _place(data_dir: Path, ref: str, source: Path) -> Path:
    """``source`` の bytes を ``ref`` の置き場へ置く（別の書き手が作った既存 CSV の再現）。"""
    out = tick_m1.m1_csv_path(ref=ref, data_dir=data_dir)
    out.write_bytes(source.read_bytes())
    return out


def _spread_csv(data_dir: Path, days) -> Path:
    """台帳外 ref に point を明示して spread 付き CSV を作る（段階 3 本体の書き手が作る形）。"""
    return _run(tick_m1.build_m1_from_ticks, _UNREGISTERED, data_dir, days[0], days[-1],
                point=_snapshot_point())


def _plain_csv(data_dir: Path, days) -> Path:
    """spread 無し CSV（現行の書き手が作る形）。"""
    return _run(tick_m1.build_m1_from_ticks, "zz_plain_source", data_dir, days[0], days[-1])


def _tear_tail(path: Path) -> None:
    """末尾へ書き掛けの行を足す（追記がクラッシュした跡＝自己修復の全構築へ落ちる状態）。"""
    with open(path, "a", encoding="utf-8") as fh:
        fh.write("2026-09-09 00:00:00,660")


def _header(path: Path) -> str:
    return path.read_text(encoding="utf-8").splitlines()[0]


def _spy(monkeypatch, module, name: str) -> "list[tuple]":
    """``module.name`` を包み、発行ごとに引数を記録する Test Spy（モジュール属性の継ぎ目）。"""
    real = getattr(module, name)
    calls: "list[tuple]" = []

    def spy(*args, **kwargs):
        calls.append(args)
        return real(*args, **kwargs)

    monkeypatch.setattr(module, name, spy)
    return calls


# =====================================================================
# 6. 台帳の宣言 == 台帳外 ref に同じ point を明示
# =====================================================================
def test_a_series_built_from_the_ledger_declaration_matches_an_explicit_point(tmp_path, monkeypatch):
    """build: 宣言から作った CSV は、同じ point を明示した台帳外 ref の CSV と byte 一致。"""
    # Arrange
    _register(monkeypatch, tmp_path, _DECLARED, _PAIR)
    days = _put_days(tmp_path, 2)

    # Act
    from_ledger = _run(tick_m1.build_m1_from_ticks, _DECLARED, tmp_path, days[0], days[-1])
    explicit = _spread_csv(tmp_path, days)

    # Assert
    assert _header(from_ledger).endswith(",spread")  # 空振り防止（両方 spread 無しでも一致してしまう）
    assert from_ledger.read_bytes() == explicit.read_bytes()


def test_an_append_from_the_ledger_declaration_matches_an_explicit_point(tmp_path, monkeypatch):
    """append: 宣言で追記した CSV は、同じ point を明示して追記した台帳外 ref の CSV と byte 一致。"""
    # Arrange
    _register(monkeypatch, tmp_path, _DECLARED, _PAIR)
    _put_day(tmp_path, _day(0))
    _run(tick_m1.build_m1_from_ticks, _DECLARED, tmp_path, _day(0), _day(0))
    _spread_csv(tmp_path, [_day(0)])
    _put_day(tmp_path, _day(1))

    # Act
    from_ledger = _run(tick_m1.append_m1_from_ticks, _DECLARED, tmp_path, _day(0), _day(1))
    explicit = _run(tick_m1.append_m1_from_ticks, _UNREGISTERED, tmp_path, _day(0), _day(1),
                    point=_snapshot_point())

    # Assert
    assert len(from_ledger.read_text(encoding="utf-8").splitlines()) == 5  # ヘッダ + 2 日 × 2 分
    assert from_ledger.read_bytes() == explicit.read_bytes()


# =====================================================================
# 7. 登録済みの ref に point を明示したら拒否（T2）
# =====================================================================
def _as_in_the_real_ledger(monkeypatch, tmp_path) -> None:
    """実台帳の ref はそのまま使う（登録しない）。"""


def _as_declared(monkeypatch, tmp_path) -> None:
    _register(monkeypatch, tmp_path, _DECLARED, _PAIR)


def _existing_csv_and_next_day(ref: str, tmp_path: Path) -> "tuple[Path, bytes, int]":
    """``ref`` の既存 CSV（宣言どおり）と、追記され得る翌日のティックを置く。"""
    _put_day(tmp_path, _day(0))
    out = _run(tick_m1.build_m1_from_ticks, ref, tmp_path, _day(0), _day(0))
    _put_day(tmp_path, _day(1))
    return out, out.read_bytes(), out.stat().st_mtime_ns


@pytest.mark.parametrize("entry", [_BUILD, _APPEND])
@pytest.mark.parametrize(
    ("ref", "registration"),
    [pytest.param("jp225_tick", _as_in_the_real_ledger, id="jp225_tick"),
     pytest.param("jp225_mt5", _as_in_the_real_ledger, id="jp225_mt5"),
     pytest.param(_DECLARED, _as_declared, id="declared")],
)
def test_an_explicit_point_for_a_registered_ref_is_refused_before_any_io(
    tmp_path, monkeypatch, entry, ref, registration
):
    """台帳が point の唯一源なので、登録済み ref へ呼出側の point は受けない（宣言と同じ値でも常に拒否）。

    ファイル bytes・mtime 不変、parquet を 1 回も読まない（全書換の第 2 の源を IO の前に断つ）。
    """
    # Arrange
    registration(monkeypatch, tmp_path)
    out, before, mtime = _existing_csv_and_next_day(ref, tmp_path)
    reads = _spy(monkeypatch, tick_m1.pd, "read_parquet")

    # Act / Assert
    with pytest.raises(ValueError, match="台帳"):
        _run(entry, ref, tmp_path, _day(0), _day(1), point=0.1)
    assert out.read_bytes() == before
    assert out.stat().st_mtime_ns == mtime
    assert reads == []


# =====================================================================
# 8. 食い違いは拒否（全書換へ落ちない）・一致なら従来どおり
# =====================================================================
# 型は Assert で名指しする。pytest.raises を Exception で受けるのは、未実装のとき
# 「止まらず全書換した」ことが DID NOT RAISE として振る舞いで見えるようにするため。
@pytest.mark.parametrize("entry", [_BUILD, _APPEND])
def test_a_spread_file_under_an_undeclared_ref_is_refused(tmp_path, monkeypatch, entry):
    """spread 付き CSV × 宣言 None（R-2/Y-2 の典型: point を渡し忘れた書き手が全書換していた）。"""
    # Arrange
    _register(monkeypatch, tmp_path, _UNDECLARED, None)
    _put_day(tmp_path, _day(0))
    out = _place(tmp_path, _UNDECLARED, _spread_csv(tmp_path, [_day(0)]))
    _put_day(tmp_path, _day(1))
    before = out.read_bytes()

    # Act
    with pytest.raises(Exception) as caught:
        _run(entry, _UNDECLARED, tmp_path, _day(0), _day(1))

    # Assert
    assert caught.type is tick_m1.SpreadSchemaMismatch
    assert out.read_bytes() == before


@pytest.mark.parametrize("entry", [_BUILD, _APPEND])
def test_a_plain_file_under_a_declared_ref_is_refused(tmp_path, monkeypatch, entry):
    """spread 無し CSV × 宣言あり（宣言を足しただけで既存系列を全書換させない）。"""
    # Arrange
    _register(monkeypatch, tmp_path, _DECLARED, _PAIR)
    _put_day(tmp_path, _day(0))
    out = _place(tmp_path, _DECLARED, _plain_csv(tmp_path, [_day(0)]))
    _put_day(tmp_path, _day(1))
    before = out.read_bytes()

    # Act
    with pytest.raises(Exception) as caught:
        _run(entry, _DECLARED, tmp_path, _day(0), _day(1))

    # Assert
    assert caught.type is tick_m1.SpreadSchemaMismatch
    assert out.read_bytes() == before


def test_a_torn_spread_file_under_an_undeclared_ref_is_refused_not_rebuilt(tmp_path, monkeypatch):
    """末尾破損 × 食い違い: 自己修復の全構築へ落ちず拒否する（末尾破損も全書換の入口だった）。"""
    # Arrange
    _register(monkeypatch, tmp_path, _UNDECLARED, None)
    _put_day(tmp_path, _day(0))
    out = _place(tmp_path, _UNDECLARED, _spread_csv(tmp_path, [_day(0)]))
    _tear_tail(out)
    _put_day(tmp_path, _day(1))
    before = out.read_bytes()

    # Act
    with pytest.raises(Exception) as caught:
        _run(tick_m1.append_m1_from_ticks, _UNDECLARED, tmp_path, _day(0), _day(1))

    # Assert
    assert caught.type is tick_m1.SpreadSchemaMismatch
    assert out.read_bytes() == before


def test_a_torn_file_whose_spread_agrees_is_still_self_repaired(tmp_path, monkeypatch):
    """回帰固定: 末尾破損でも spread 有無が一致なら従来どおり全構築で自己修復する（ISSUE-455 系）。"""
    # Arrange
    _register(monkeypatch, tmp_path, _UNDECLARED, None)
    days = _put_days(tmp_path, 2)
    out = _place(tmp_path, _UNDECLARED, _plain_csv(tmp_path, days[:1]))
    _tear_tail(out)

    # Act
    _run(tick_m1.append_m1_from_ticks, _UNDECLARED, tmp_path, days[0], days[-1])

    # Assert: 同じ tick 木から一括 build した全文と一致（壊れ行は消え、翌日分まで揃う）。
    assert out.read_bytes() == _plain_csv(tmp_path, days).read_bytes()


@pytest.mark.parametrize(
    "prepare",
    [pytest.param(lambda out: None, id="no_file"),
     pytest.param(lambda out: out.write_bytes(b""), id="empty_file")],
)
def test_a_missing_or_empty_file_is_not_checked(tmp_path, monkeypatch, prepare):
    """境界: ファイル無し・空ファイルは照合しない（宣言ありでそのまま spread 付きを書く）。"""
    # Arrange
    _register(monkeypatch, tmp_path, _DECLARED, _PAIR)
    _put_day(tmp_path, _day(0))
    out = tick_m1.m1_csv_path(ref=_DECLARED, data_dir=tmp_path)
    prepare(out)

    # Act
    _run(tick_m1.append_m1_from_ticks, _DECLARED, tmp_path, _day(0), _day(0))

    # Assert
    assert _header(out).endswith(",spread")


# =====================================================================
# T1. 例外の型（全書換への逃げ道に捕まらない）
# =====================================================================
def test_the_mismatch_is_a_runtime_error_and_not_a_value_error():
    """追記の except ValueError（全構築への逃げ道）に型として捕まらない基底であること。"""
    # Arrange / Act / Assert
    assert issubclass(tick_m1.SpreadSchemaMismatch, RuntimeError)
    assert not issubclass(tick_m1.SpreadSchemaMismatch, ValueError)


class _MismatchOnAppendWriter:
    """追記の段で食い違いを送出する書き手（M1Writer の具象・呼ばれた書込を記録する）。"""

    def __init__(self) -> None:
        self.whole_writes: "list[Path]" = []

    def write_whole(self, m1, path) -> None:
        self.whole_writes.append(Path(path))

    def append(self, m1_new, path) -> None:
        raise tick_m1.SpreadSchemaMismatch(f"{path}: 追記の段で検出した食い違い")


def test_a_mismatch_raised_inside_the_self_repair_try_propagates_without_a_rewrite(
    tmp_path, monkeypatch
):
    """writer.append は except ValueError の内側で呼ばれる。そこで食い違いが出ても全構築しない。"""
    # Arrange
    _register(monkeypatch, tmp_path, _UNDECLARED, None)
    _put_day(tmp_path, _day(0))
    _run(tick_m1.build_m1_from_ticks, _UNDECLARED, tmp_path, _day(0), _day(0))
    _put_day(tmp_path, _day(1))
    writer = _MismatchOnAppendWriter()

    # Act
    with pytest.raises(Exception) as caught:
        _run(tick_m1.append_m1_from_ticks, _UNDECLARED, tmp_path, _day(0), _day(1), writer=writer)

    # Assert
    assert caught.type is tick_m1.SpreadSchemaMismatch
    assert writer.whole_writes == []


# =====================================================================
# 計算量（Test Spy・発行 − 使用 = 0・規模 2 点・回数は焼き込まない）
# =====================================================================
def _spy_snapshot_reads(monkeypatch) -> "list[tuple[tuple, float]]":
    """sss.load_snapshot を包み、発行ごとに（読んだ組, その読込で得た point_size）を記録する Test Spy。

    point_size は既存の公開経路 sss.spec_fields で、読込の戻り値そのものから引く（被検査の新関数を
    通さない）。継ぎ目はモジュール属性（marketdata.spread_point は marketdata.symbol_spec_snapshot の
    関数をモジュール属性経由で呼ぶ）。
    """
    real = sss.load_snapshot
    reads: "list[tuple[tuple, float]]" = []

    def spy(*args, **kwargs):
        snapshot = real(*args, **kwargs)
        reads.append((args, sss.spec_fields(snapshot)["point_size"]))
        return snapshot

    monkeypatch.setattr(sss, "load_snapshot", spy)
    return reads


def _spy_spent_points(monkeypatch) -> "list[float]":
    """quote_spread.minute_spread_points（出力の spread 列を数える唯一の口）へ渡された point を記録する。"""
    real = quote_spread.minute_spread_points
    spent: "list[float]" = []

    def spy(*args, **kwargs):
        spent.append(kwargs["point"])
        return real(*args, **kwargs)

    monkeypatch.setattr(quote_spread, "minute_spread_points", spy)
    return spent


def _used_reads(reads: "list[tuple[tuple, float]]", spent: "list[float]") -> int:
    """出力の spread に使われた読込の数＝読んだ組のうち、その point が spread の計算へ渡った組の数。

    同じ組を 2 回読んでも使用は 1（2 回目の読込は出力に何も足さない）。読んだ point と別の point で
    spread を数えたら、その読込の使用は 0。
    """
    return len({pair for pair, point in reads if point in spent})


def test_cx1_the_snapshot_is_read_only_for_the_point_that_is_spent(tmp_path, monkeypatch):
    """CX-1: 追記を 2 回と 20 回: スナップショット読込の発行 − spread に使った読込 = 0、発行は 2 点で等しい。"""
    reads = _spy_snapshot_reads(monkeypatch)
    spent = _spy_spent_points(monkeypatch)
    issued_by_scale = []
    for n_calls in (2, 20):
        # Arrange: 初日を build 済み・キャッシュ空・読込の記録を空にする。
        data_dir = tmp_path / f"calls{n_calls}"
        _register(monkeypatch, data_dir, _DECLARED, _PAIR)
        _put_day(data_dir, _day(0))
        out = _run(tick_m1.build_m1_from_ticks, _DECLARED, data_dir, _day(0), _day(0))
        rows_before = len(out.read_text(encoding="utf-8").splitlines())
        _spread_point()._point_size_of_snapshot.cache_clear()
        reads.clear()
        spent.clear()

        # Act: 1 日ずつ n_calls 回追記する。
        for k in range(1, n_calls + 1):
            _put_day(data_dir, _day(k))
            _run(tick_m1.append_m1_from_ticks, _DECLARED, data_dir, _day(0), _day(k))

        # Assert
        appended = len(out.read_text(encoding="utf-8").splitlines()) - rows_before
        assert appended > 0  # 空振り防止
        assert _header(out).endswith(",spread")
        used = _used_reads(reads, spent)  # 観測値: 読んだ組のうち point が spread の計算へ渡った組
        assert len(reads) - used == 0, f"{n_calls} 回: 読込 {len(reads)} − 使用 {used} ≠ 0"
        issued_by_scale.append(len(reads))

    assert issued_by_scale[0] == issued_by_scale[1], (
        f"追記を 2 → 20 回にしたらスナップショット読込が {issued_by_scale[0]} → {issued_by_scale[1]}"
        " へ増えました（呼出ごとに point を読み直しています）。"
    )


@pytest.mark.parametrize("entry", [_BUILD, _APPEND])
@pytest.mark.parametrize("n_days", [1, 4])
@pytest.mark.parametrize("ref", [_UNDECLARED, _UNREGISTERED])
def test_cx2_an_undeclared_series_never_reads_a_snapshot(tmp_path, monkeypatch, entry, n_days, ref):
    """CX-2: 宣言無し（登録済み None・台帳外で point 無し）は build / append の 2 規模ともスナップショット読込 0。"""
    # Arrange
    _register(monkeypatch, tmp_path, _UNDECLARED, None)
    days = _put_days(tmp_path, n_days)
    reads = _spy(monkeypatch, sss, "load_snapshot")

    # Act
    out = _run(entry, ref, tmp_path, days[0], days[-1])

    # Assert: spread 列が無い＝point を 1 つも使っていない。
    used = 0
    assert not _header(out).endswith(",spread")
    assert len(reads) - used == 0


@pytest.mark.parametrize("gap_days", [0, 3], ids=["same_day_reread", "no_new_day_file"])
def test_cx3_an_append_without_new_minutes_does_not_read_the_snapshot(tmp_path, monkeypatch, gap_days):
    """CX-3: キャッシュ空で新しい分の無い追記は point を解決しない（遅延解決・発行 − 使用 = 0）。"""
    # Arrange
    _register(monkeypatch, tmp_path, _DECLARED, _PAIR)
    _put_day(tmp_path, _day(0))
    out = _run(tick_m1.build_m1_from_ticks, _DECLARED, tmp_path, _day(0), _day(0))
    before = out.read_bytes()
    _spread_point()._point_size_of_snapshot.cache_clear()
    reads = _spy(monkeypatch, sss, "load_snapshot")

    # Act
    _run(tick_m1.append_m1_from_ticks, _DECLARED, tmp_path, _day(gap_days), _day(gap_days))

    # Assert: 書いた行 0 ＝ point の使用 0。
    used = 0
    assert out.read_bytes() == before
    assert len(reads) - used == 0


def _measure_lookups(monkeypatch, act) -> "tuple[int, int]":
    """``act`` の間のヘッダ読取と宣言照会の発行数（spread_point 経由の照会も含む）。"""
    headers = _spy(monkeypatch, tick_m1, "_existing_csv_header")
    lookups = _spy(monkeypatch, dataset_registry, "spread_point_snapshot_of")
    act()
    return len(headers), len(lookups)


def _build_twice(monkeypatch, data_dir: Path, n_days: int) -> "tuple[int, int]":
    """宣言ありの ref を build 済みにし、同じ範囲をもう一度 build したときの発行数。"""
    _register(monkeypatch, data_dir, _DECLARED, _PAIR)
    days = _put_days(data_dir, n_days)
    _run(tick_m1.build_m1_from_ticks, _DECLARED, data_dir, days[0], days[-1])
    return _measure_lookups(monkeypatch, lambda: _run(
        tick_m1.build_m1_from_ticks, _DECLARED, data_dir, days[0], days[-1]))


def _append_days(monkeypatch, data_dir: Path, n_new: int) -> "tuple[int, int]":
    """宣言ありの ref を初日で build 済みにし、``n_new`` 日を 1 回で追記したときの発行数。"""
    _register(monkeypatch, data_dir, _DECLARED, _PAIR)
    _put_day(data_dir, _day(0))
    _run(tick_m1.build_m1_from_ticks, _DECLARED, data_dir, _day(0), _day(0))
    for k in range(1, n_new + 1):
        _put_day(data_dir, _day(k))
    return _measure_lookups(monkeypatch, lambda: _run(
        tick_m1.append_m1_from_ticks, _DECLARED, data_dir, _day(0), _day(n_new)))


def test_cx4_header_reads_and_ledger_lookups_do_not_grow_with_days_in_a_build(tmp_path, monkeypatch):
    """CX-4（build）: ヘッダ読取と宣言照会の発行は日数 1 日と 10 日で等しい（日ごとに照合しない）。"""
    # Arrange / Act
    small = _build_twice(monkeypatch, tmp_path / "d1", 1)
    large = _build_twice(monkeypatch, tmp_path / "d10", 10)

    # Assert
    assert small[0] > 0  # 空振り防止（照合がヘッダを読んでいる）
    assert small == large, f"(ヘッダ読取, 宣言照会): 1 日 {small} / 10 日 {large}"


def test_cx4_header_reads_and_ledger_lookups_do_not_grow_with_days_in_an_append(tmp_path, monkeypatch):
    """CX-4（append）: 追記する日数 1 日と 10 日で発行が等しい。"""
    # Arrange / Act
    small = _append_days(monkeypatch, tmp_path / "d1", 1)
    large = _append_days(monkeypatch, tmp_path / "d10", 10)

    # Assert
    assert small[0] > 0
    assert small == large, f"(ヘッダ読取, 宣言照会): 1 日 {small} / 10 日 {large}"


def test_cx4_the_torn_tail_fallback_does_not_check_twice(tmp_path, monkeypatch):
    """CX-4（末尾破損フォールバック）: 発行は同じ範囲の build と等しい（全構築の段で照合し直さない）。"""
    # Arrange
    built = _build_twice(monkeypatch, tmp_path / "build", 2)
    data_dir = tmp_path / "torn"
    _register(monkeypatch, data_dir, _DECLARED, _PAIR)
    days = _put_days(data_dir, 2)
    out = _run(tick_m1.build_m1_from_ticks, _DECLARED, data_dir, days[0], days[0])
    _tear_tail(out)

    # Act
    fallback = _measure_lookups(monkeypatch, lambda: _run(
        tick_m1.append_m1_from_ticks, _DECLARED, data_dir, days[0], days[-1]))

    # Assert
    assert _header(out).endswith(",spread")  # 空振り防止（全構築で書き直された）
    assert fallback == built, f"(ヘッダ読取, 宣言照会): build {built} / フォールバック {fallback}"


def test_cx4_the_header_lag_fallback_does_not_check_twice(tmp_path, monkeypatch):
    """CX-4（ISSUE-455 のヘッダ遅れフォールバック）: 発行は健全な追記と等しい（全構築の段で照合し直さない）。"""
    # Arrange: 健全な追記（同じ規模）の発行。
    healthy = _append_days(monkeypatch, tmp_path / "healthy", 1)
    # up/dn の無い旧 6 列の既存 CSV（spread は無い＝宣言 None と一致）。
    data_dir = tmp_path / "lag"
    _register(monkeypatch, data_dir, _UNDECLARED, None)
    days = _put_days(data_dir, 2)
    out = tick_m1.m1_csv_path(ref=_UNDECLARED, data_dir=data_dir)
    out.write_text(
        "date,open,high,low,close,volume\n2026-09-01 00:00:00,66000.5,66005.5,66000.5,66005.5,3.0\n",
        encoding="utf-8",
    )

    # Act
    lag = _measure_lookups(monkeypatch, lambda: _run(
        tick_m1.append_m1_from_ticks, _UNDECLARED, data_dir, days[0], days[-1]))

    # Assert
    assert _header(out) == "date,open,high,low,close,volume,up,dn"  # 空振り防止（全構築で是正された）
    assert lag[0] == healthy[0], f"ヘッダ読取: 健全な追記 {healthy[0]} / ヘッダ遅れ {lag[0]}"
