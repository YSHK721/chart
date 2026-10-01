"""spread 列を持つ新系列 ``jp225_mt5_spread`` の台帳宣言（ISSUE-511 段階 3 の段階 7a）。

用語（初出定義）:
    宣言
        ＝ 台帳記述子の ``spread_point_snapshot``（``marketdata.dataset_registry``）。値は銘柄仕様
          スナップショットの所在（サーバ名, 銘柄名）であり point の値は持たない。None＝spread 列を
          持たない系列。
    置き場（series）
        ＝ ref の保存物の名前（``<series>_m1.csv`` と ``rollups/<series>/``）。解決は
          ``dataset_registry.series_of``。
    誤発火
        ＝ 台帳の宣言と既存 CSV の列形が食い違っていないのに
          ``tick_m1.SpreadSchemaMismatch`` が上がること。

なぜ新しい ref か（依頼者裁定 2026-09-17）:
    既存 CSV へ spread 列を足す方式は、書き手の列形照合を迂回して**既存 CSV の全書換**（R-2/Y-2）を
    自分で開けることになる。よって spread は**新しい系列**に作り、既存 5 ファイルは 1 バイトも
    触らない。台帳の 2 行（記述子と宣言）を戻せば可逆である。

本検定が固定するもの:
  N-1 台帳が新 ref の spread を宣言している（宣言の所在の値ピン）。
  N-2 記述子の属性は ``jp225_mt5`` に倣う（同じ木・同じ基準・同じベンダ）。
  N-3 置き場が既存のどの系列とも衝突しない。
  N-4 新 ref は spread 列つきの M1 を書き、その値は宣言した point で数えたものと byte 一致する。
  N-5 既存のティック ref（``jp225_tick`` / ``jp225_mt5``）の列形は変わらない（spread 列を持たない）。
  N-6 段階 4・5 の Fail-Stop が誤発火しない（既存ファイルの無い初回は素通し）。
  N-7 負の対照: 宣言と食い違う既存 CSV では Fail-Stop が実際に上がる（N-6 が空振りでないことの実証）。
  CX-N1 継ぎ目は ``spy_snapshot_reads``（銘柄仕様スナップショットの読込を数える）と
        ``spy_spent_points``（spread の計算へ実際に渡った point を数える）の 2 つ。
        読込 − spread に使った読込 = 0（追記 2 回と 20 回の 2 点で、発行は増えない）。
  CX-N2 継ぎ目 ``tick_m1._existing_csv_header`` と ``dataset_registry.spread_point_snapshot_of``:
        発行は build の日数 1 日と 10 日で等しい（日ごとに照合し直さない）。

計算量の規約（絶対命令 2026-08-28）: 回数そのものは期待値に焼き込まない。固定するのは
「発行 − 使用 = 0」と「入力の規模を変えても発行が増えないこと」だけである。

**書込はすべて ``tmp_path``**（``data/marketdata/**`` は 1 バイトも触らない。実データの生成は
段階 7b＝別の明示的な操作）。合成系列の組み立ては ``marketdata/tests/spread_series_fixture.py``
が唯一源。構造: Arrange-Act-Assert（AAA）。
"""
from __future__ import annotations

from pathlib import Path

import pytest

from marketdata import dataset_registry, tick_m1
from marketdata import csv_schema as _csv_schema
from marketdata import symbol_spec_snapshot as sss
from marketdata.dataset_registry import REGISTRY
from spread_series_fixture import (
    LEDGER_BASIS,
    SNAPSHOT_PAIR,
    cleared_point_cache,  # noqa: F401  （import した先で autouse になる共有 fixture）
    day,
    header_of,
    put_day,
    put_days,
    run_writer,
    snapshot_point,
    spy,
    spy_snapshot_reads,
    spy_spent_points,
    used_reads,
)

#: spread 列を持つ新系列の ref（依頼者裁定 2026-09-17・既存の並び「銘柄＋供給元＋属性」に従う）。
NEW_REF = "jp225_mt5_spread"
#: 比較対象の既存ティック ref（列形が変わらないことの壁）。
_EXISTING_TICK_REFS = ["jp225_tick", "jp225_mt5"]
#: 台帳外 ref（従来どおり point の明示が効く側）。byte 一致の突合に使う。
_UNREGISTERED = "zz_new_ref_unregistered"


def _columns(path: Path) -> "list[str]":
    """出力 CSV の先頭行を列名へ割る。"""
    return header_of(path).split(",")


# =====================================================================
# N-1 / N-2. 台帳の宣言と記述子の属性
# =====================================================================
def test_the_new_series_declares_where_its_spread_point_comes_from():
    """N-1: 新 ref の宣言は実在するスナップショットの所在（サーバ名, 銘柄名）である。"""
    # Arrange / Act
    declared = dataset_registry.spread_point_snapshot_of(NEW_REF)

    # Assert
    assert declared == (sss.OANDA_JAPAN_MT5_LIVE, "JP225")
    assert sss.snapshot_path(*declared).is_file(), f"宣言した組のスナップショットが無い: {declared}"


def test_the_new_series_descriptor_follows_the_mt5_one():
    """N-2: 記述子は ``jp225_mt5`` に倣う（同じ木・同じ基準・同じベンダ・ロールアップ経路）。"""
    # Arrange
    new = REGISTRY[NEW_REF]
    mt5 = REGISTRY["jp225_mt5"]

    # Act / Assert
    assert (new.tick, new.rollup, new.clamp_outliers) == (True, True, True)
    assert new.symbol == mt5.symbol
    assert new.tick_token == mt5.tick_token  # 同じ木を読む（枝を分けない）
    assert new.price_basis == mt5.price_basis == tick_m1.PRICE_BASIS_BID
    assert new.vendor == mt5.vendor


# =====================================================================
# N-3. 置き場が既存と衝突しない
# =====================================================================
def test_the_new_series_does_not_collide_with_any_existing_storage(tmp_path):
    """N-3: series も実 CSV パスも台帳の中で一意（既存の置き場へ書き込まない）。"""
    # Arrange
    series = [dataset_registry.series_of(ref) for ref in REGISTRY]
    paths = [d.path for d in REGISTRY.values()]

    # Act
    new_path = tick_m1.m1_csv_path(ref=NEW_REF, data_dir=tmp_path)

    # Assert
    assert len(series) == len(set(series)), f"series が重複している: {sorted(series)}"
    assert len(paths) == len(set(paths)), "実 CSV パスが重複している"
    assert dataset_registry.series_of(NEW_REF) == NEW_REF
    assert new_path == tmp_path / f"{NEW_REF}_m1.csv"
    assert new_path != tick_m1.m1_csv_path(ref="jp225_mt5", data_dir=tmp_path)


# =====================================================================
# N-4. 新 ref は spread 列つきの M1 を書く（tmp_path 上）
# =====================================================================
def test_the_new_series_writes_a_spread_column_priced_by_the_declared_point(tmp_path):
    """N-4: 宣言から書いた M1 は、同じ point を明示した台帳外 ref の M1 と byte 一致する。

    byte 一致にするのは「spread 列が在る」だけでは値の出所を固定できないためである
    （別の point で数えても列は在る）。
    """
    # Arrange
    days = put_days(tmp_path, 2)

    # Act
    from_ledger = run_writer(tick_m1.build_m1_from_ticks, NEW_REF, tmp_path, days[0], days[-1])
    explicit = run_writer(
        tick_m1.build_m1_from_ticks, _UNREGISTERED, tmp_path, days[0], days[-1],
        point=snapshot_point(), price_basis=LEDGER_BASIS,
    )

    # Assert
    assert _columns(from_ledger)[-1] == _csv_schema.SPREAD_COLUMN  # 空振り防止
    assert from_ledger.read_bytes() == explicit.read_bytes()


# =====================================================================
# N-5. 既存のティック ref の列形は変わらない
# =====================================================================
@pytest.mark.parametrize("ref", _EXISTING_TICK_REFS)
def test_an_existing_tick_series_still_writes_no_spread_column(tmp_path, ref):
    """N-5: 宣言を 1 件足しても、宣言の無い既存 ref は spread 列を持たないままである。"""
    # Arrange
    days = put_days(tmp_path, 2)

    # Act
    out = run_writer(tick_m1.build_m1_from_ticks, ref, tmp_path, days[0], days[-1])

    # Assert
    assert _csv_schema.SPREAD_COLUMN not in _columns(out)


def test_an_existing_tick_series_is_byte_identical_to_a_plain_unregistered_one(tmp_path):
    """N-5（値まで）: 宣言の無い既存 ref の出力は、spread を持たない台帳外 ref と byte 一致する。"""
    # Arrange
    days = put_days(tmp_path, 2)

    # Act
    existing = run_writer(tick_m1.build_m1_from_ticks, "jp225_mt5", tmp_path, days[0], days[-1])
    plain = run_writer(
        tick_m1.build_m1_from_ticks, "zz_new_ref_plain", tmp_path, days[0], days[-1],
        price_basis=LEDGER_BASIS,
    )

    # Assert
    assert existing.read_bytes() == plain.read_bytes()


# =====================================================================
# N-6 / N-7. Fail-Stop（段階 4・5）は誤発火せず、食い違いでは上がる
# =====================================================================
@pytest.mark.parametrize(
    "prepare",
    [pytest.param(lambda out: None, id="no_file"),
     pytest.param(lambda out: out.write_bytes(b""), id="empty_file")],
)
def test_the_startup_check_passes_for_a_new_series_with_no_existing_file(tmp_path, prepare):
    """N-6: 新 ref の既存ファイルが無い／空の初回は照合を素通しする（常駐が起動できる）。"""
    # Arrange
    prepare(tick_m1.m1_csv_path(ref=NEW_REF, data_dir=tmp_path))

    # Act / Assert（送出しないことが主張なので、例外はそのまま失敗になる）
    tick_m1.check_series_schema(NEW_REF, data_dir=tmp_path)


def test_the_startup_check_passes_for_a_file_the_new_series_just_wrote(tmp_path):
    """N-6（続き）: 自分が書いた spread 付き CSV に対しても誤発火しない。"""
    # Arrange
    put_day(tmp_path, day(0))
    out = run_writer(tick_m1.build_m1_from_ticks, NEW_REF, tmp_path, day(0), day(0))

    # Act / Assert
    assert _columns(out)[-1] == _csv_schema.SPREAD_COLUMN  # 空振り防止
    tick_m1.check_series_schema(NEW_REF, data_dir=tmp_path)


@pytest.mark.parametrize("ref", _EXISTING_TICK_REFS)
def test_the_startup_check_passes_for_the_existing_series(tmp_path, ref):
    """N-6（既存側）: 宣言を足したことで既存 ref の起動時照合が落ちるようにはならない。"""
    # Arrange
    put_day(tmp_path, day(0))
    run_writer(tick_m1.build_m1_from_ticks, ref, tmp_path, day(0), day(0))

    # Act / Assert
    tick_m1.check_series_schema(ref, data_dir=tmp_path)


def test_the_startup_check_still_stops_a_file_that_disagrees_with_the_declaration(tmp_path):
    """N-7（負の対照）: spread 無しの CSV を新 ref の置き場へ置くと Fail-Stop が上がる。

    N-6 が「常に素通し」へ弱体化していないことの実証である。
    """
    # Arrange: spread を持たない CSV を新 ref の置き場へ置く。
    put_day(tmp_path, day(0))
    plain = run_writer(
        tick_m1.build_m1_from_ticks, "zz_new_ref_plain_src", tmp_path, day(0), day(0),
        price_basis=LEDGER_BASIS,
    )
    out = tick_m1.m1_csv_path(ref=NEW_REF, data_dir=tmp_path)
    out.write_bytes(plain.read_bytes())
    before = out.read_bytes()

    # Act
    with pytest.raises(Exception) as caught:
        tick_m1.check_series_schema(NEW_REF, data_dir=tmp_path)

    # Assert
    assert caught.type is tick_m1.SpreadSchemaMismatch
    assert out.read_bytes() == before  # 照合は読むだけ（書かない）


# =====================================================================
# CX-N1. 読込 − 使用 = 0（追記 2 回と 20 回）
# =====================================================================
def test_cx_n1_the_new_series_reads_the_snapshot_only_for_the_point_it_spends(tmp_path, monkeypatch):
    """CX-N1: 追記 2 回と 20 回で 読込 − 使用 = 0、発行は 2 点で等しい（呼出ごとに読み直さない）。"""
    reads = spy_snapshot_reads(monkeypatch)
    spent = spy_spent_points(monkeypatch)
    issued_by_scale = []
    for n_calls in (2, 20):
        # Arrange: 初日を build 済み・point の記憶は空・記録を空にする。
        data_dir = tmp_path / f"calls{n_calls}"
        put_day(data_dir, day(0))
        out = run_writer(tick_m1.build_m1_from_ticks, NEW_REF, data_dir, day(0), day(0))
        rows_before = len(out.read_text(encoding="utf-8").splitlines())
        from marketdata import spread_point

        spread_point.forget_resolved_points()
        reads.clear()
        spent.clear()

        # Act: 1 日ずつ n_calls 回追記する。
        for k in range(1, n_calls + 1):
            put_day(data_dir, day(k))
            run_writer(tick_m1.append_m1_from_ticks, NEW_REF, data_dir, day(0), day(k))

        # Assert
        appended = len(out.read_text(encoding="utf-8").splitlines()) - rows_before
        assert appended > 0  # 空振り防止（実際に書いている）
        assert _columns(out)[-1] == _csv_schema.SPREAD_COLUMN
        used = used_reads(reads, spent)
        assert len(reads) - used == 0, f"{n_calls} 回: 読込 {len(reads)} − 使用 {used} ≠ 0"
        issued_by_scale.append(len(reads))

    assert issued_by_scale[0] == issued_by_scale[1], (
        f"追記を 2 → 20 回にしたらスナップショット読込が {issued_by_scale[0]} →"
        f" {issued_by_scale[1]} へ増えました（呼出ごとに point を読み直しています）。"
    )


def test_cx_n1_the_existing_series_reads_no_snapshot_at_all(tmp_path, monkeypatch):
    """CX-N1（対照）: 宣言の無い既存 ref は 1 日と 4 日の 2 点ともスナップショット読込 0。

    使わない point を読まない（宣言を 1 件足したせいで無関係な系列が読み始めない）。
    """
    for n_days in (1, 4):
        # Arrange
        data_dir = tmp_path / f"days{n_days}"
        days = put_days(data_dir, n_days)
        reads = spy(monkeypatch, sss, "load_snapshot")

        # Act
        out = run_writer(tick_m1.build_m1_from_ticks, "jp225_mt5", data_dir, days[0], days[-1])

        # Assert: spread 列が無い＝point を 1 つも使っていない。
        used = 0
        assert _csv_schema.SPREAD_COLUMN not in _columns(out)
        assert len(reads) - used == 0


# =====================================================================
# CX-N2. ヘッダ読取・台帳照会は日数で増えない
# =====================================================================
def _issued_for_a_build(monkeypatch, data_dir: Path, n_days: int) -> "tuple[int, int]":
    """新 ref を ``n_days`` 日で build したときの（ヘッダ読取, 台帳照会）の発行数。"""
    days = put_days(data_dir, n_days)
    headers = spy(monkeypatch, tick_m1, "_existing_csv_header")
    lookups = spy(monkeypatch, dataset_registry, "spread_point_snapshot_of")
    out = run_writer(tick_m1.build_m1_from_ticks, NEW_REF, data_dir, days[0], days[-1])
    assert _columns(out)[-1] == _csv_schema.SPREAD_COLUMN  # 空振り防止
    return len(headers), len(lookups)


def test_cx_n2_header_reads_and_ledger_lookups_do_not_grow_with_days(tmp_path, monkeypatch):
    """CX-N2: 発行は日数 1 日と 10 日で等しい（日ごとに照合・照会し直さない）。"""
    # Arrange / Act
    small = _issued_for_a_build(monkeypatch, tmp_path / "d1", 1)
    large = _issued_for_a_build(monkeypatch, tmp_path / "d10", 10)

    # Assert
    assert small[0] > 0  # 空振り防止（照合がヘッダを読んでいる）
    assert small == large, f"(ヘッダ読取, 台帳照会): 1 日 {small} / 10 日 {large}"
