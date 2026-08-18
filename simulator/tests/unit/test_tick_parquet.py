"""本番 tick-store（TickDataPort / TickStorePort + ParquetTickRepository）テスト。

設計（architecture-executor 確定）:
    - TickDataPort.load_ticks(symbol, start, end, columns=None) -> TickFrame
      [start,end) 半開・該当なしは空frame（例外でない）。
    - TickStorePort.write_ticks(symbol, frame_or_csv, mode="overwrite") -> TickWriteResult
      TICK_COLUMNS 準拠・mode overwrite=対象日再生成 / skip=既存日再書込しない・冪等。
    - ports.py は pyarrow / pandas を実行時 import しない（依存方向維持）。
    - ParquetTickRepository: <root>/<symbol>/year=YYYY/month=MM/day=DD/part.parquet（hive）。
      pyarrow.dataset 述語プルーニング + timestamp 厳密フィルタの 2 段。
      例外翻訳: pyarrow/pandas/OSError→DataError・列欠損→MissingBarError・非昇順→TimeOrderError。

TickFrame の concrete 型は pandas.DataFrame（設計 TBD・呼出側後確定の判断点）。
テストデータは simulator/tools/bench/synth_ticks.py（決定論的）を流用。
"""
from __future__ import annotations

import abc

from simulator.tools.bench.synth_ticks import TickGenConfig, generate_ticks


# =========================================================================
# Section 1: Port（TickDataPort / TickStorePort / ports.py 隔離）
# =========================================================================

def test_tick_data_port_declares_load_ticks_abstractmethod():
    # Arrange / Act
    from simulator.usecase.ports import TickDataPort

    # Assert: ABC かつ load_ticks が abstractmethod
    assert issubclass(TickDataPort, abc.ABC)
    assert "load_ticks" in TickDataPort.__abstractmethods__


def test_tick_store_port_declares_write_ticks_abstractmethod():
    # Arrange / Act
    from simulator.usecase.ports import TickStorePort

    # Assert: ABC かつ write_ticks が abstractmethod
    assert issubclass(TickStorePort, abc.ABC)
    assert "write_ticks" in TickStorePort.__abstractmethods__


def test_ports_module_does_not_import_pandas_or_pyarrow_at_runtime():
    # Arrange: ports.py を ast で静的解析し、実行時 import を抽出する
    import ast
    import pathlib

    from simulator.usecase import ports as ports_mod

    source = pathlib.Path(ports_mod.__file__).read_text()
    tree = ast.parse(source)

    # TYPE_CHECKING ブロック内の import は実行時に評価されないため除外する。
    type_checking_nodes: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.If):
            test = node.test
            is_type_checking = (
                (isinstance(test, ast.Name) and test.id == "TYPE_CHECKING")
                or (isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING")
            )
            if is_type_checking:
                for child in ast.walk(node):
                    type_checking_nodes.add(id(child))

    runtime_modules: set[str] = set()
    for node in ast.walk(tree):
        if id(node) in type_checking_nodes:
            continue
        if isinstance(node, ast.Import):
            for alias in node.names:
                runtime_modules.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom) and node.module:
            runtime_modules.add(node.module.split(".")[0])

    # Assert: 依存方向維持（pandas / pyarrow を実行時 import しない）
    assert "pandas" not in runtime_modules
    assert "pyarrow" not in runtime_modules


# =========================================================================
# Section 2: _tick_frame.py（列検証・partition 列付与・_date_predicate）
# =========================================================================

def _ticks_3days():
    """2024-03-01..03 の決定論的 3 日分 tick frame。"""
    return generate_ticks(
        TickGenConfig(start_date=__import__("datetime").date(2024, 3, 1), days=3, ticks_per_day=4)
    )


def test_validate_tick_columns_raises_missing_bar_error_when_column_absent():
    import pandas as pd

    from simulator.adapter.repository._tick_frame import validate_tick_columns

    df = _ticks_3days().drop(columns=["bid"])  # 必須列欠損

    import pytest

    from simulator.domain.exceptions import MissingBarError

    with pytest.raises(MissingBarError):
        validate_tick_columns(df)


def test_validate_tick_columns_raises_time_order_error_when_not_ascending():
    from simulator.adapter.repository._tick_frame import validate_tick_columns

    df = _ticks_3days()
    # timestamp を降順に反転して非昇順を作る
    df = df.iloc[::-1].reset_index(drop=True)

    import pytest

    from simulator.domain.exceptions import TimeOrderError

    with pytest.raises(TimeOrderError):
        validate_tick_columns(df)


def test_with_partition_columns_adds_year_month_day_matching_timestamp():
    from simulator.adapter.repository._tick_frame import with_partition_columns

    df = _ticks_3days()

    out = with_partition_columns(df)

    # 各行の year/month/day が timestamp と一致する
    ts = out["timestamp"]
    assert (out["year"] == ts.dt.year).all()
    assert (out["month"] == ts.dt.month).all()
    assert (out["day"] == ts.dt.day).all()
    # 2024-03-01..03 の 3 日が含まれる
    assert sorted(out["day"].unique().tolist()) == [1, 2, 3]


def test_date_predicate_enumerates_year_month_day_covering_half_open_range():
    from datetime import datetime, timezone

    from simulator.adapter.repository._tick_frame import _date_predicate

    # ISSUE-402: `_date_predicate` の入力は epoch 秒（境界の正規化は load_ticks が
    # `simulator.domain.bar_time.epoch_seconds` で行う唯一の入口へ集約された）。
    # 検証する契約（覆う日の集合・端日の扱い）は従来と同一であり、緩めていない。
    def _epoch(*args: int) -> int:
        return int(datetime(*args, tzinfo=timezone.utc).timestamp())

    # [2024-03-01, 2024-03-04) を覆う (y,m,d) は端含めて 01/02/03 の 3 日
    days = _date_predicate(_epoch(2024, 3, 1), _epoch(2024, 3, 4))

    assert (2024, 3, 1) in days
    assert (2024, 3, 3) in days  # 端日 end の前日まで含む
    assert (2024, 3, 4) not in days  # end 当日は半開で含まない
    assert sorted(days) == [(2024, 3, 1), (2024, 3, 2), (2024, 3, 3)]


# =========================================================================
# Section 3: write_ticks（日別 Parquet 生成・冪等・skip・CSV チャンク取込）
# =========================================================================

def _count_part_files(root):
    return sorted(str(p) for p in root.rglob("part.parquet"))


def test_write_ticks_creates_one_partition_per_day_with_part_parquet(tmp_path):
    from simulator.adapter.repository.tick_parquet import ParquetTickRepository

    frame = _ticks_3days()
    repo = ParquetTickRepository(root=tmp_path)

    repo.write_ticks("JP225", frame, mode="overwrite")

    sym = tmp_path / "JP225"
    # 3 日分の year=/month=/day= 階層が生成され part.parquet が存在する
    parts = _count_part_files(sym)
    assert len(parts) == 3
    assert (sym / "year=2024" / "month=03" / "day=01" / "part.parquet").exists()
    assert (sym / "year=2024" / "month=03" / "day=02" / "part.parquet").exists()
    assert (sym / "year=2024" / "month=03" / "day=03" / "part.parquet").exists()


def test_write_ticks_overwrite_is_idempotent_row_count_unchanged(tmp_path):
    import pandas as pd

    from simulator.adapter.repository.tick_parquet import ParquetTickRepository

    frame = _ticks_3days()
    repo = ParquetTickRepository(root=tmp_path)

    repo.write_ticks("JP225", frame, mode="overwrite")
    rows_first = sum(
        len(pd.read_parquet(p)) for p in (tmp_path / "JP225").rglob("part.parquet")
    )

    # 再実行（overwrite）で行数が不変であること（冪等）
    repo.write_ticks("JP225", frame, mode="overwrite")
    rows_second = sum(
        len(pd.read_parquet(p)) for p in (tmp_path / "JP225").rglob("part.parquet")
    )

    assert rows_first == len(frame)
    assert rows_second == rows_first


def test_write_ticks_skip_does_not_rewrite_existing_day(tmp_path):
    from simulator.adapter.repository.tick_parquet import ParquetTickRepository

    frame = _ticks_3days()
    repo = ParquetTickRepository(root=tmp_path)

    repo.write_ticks("JP225", frame, mode="overwrite")
    day1 = tmp_path / "JP225" / "year=2024" / "month=03" / "day=01" / "part.parquet"
    mtime_before = day1.stat().st_mtime_ns

    # mode=skip は既存日を再書込しない（mtime 不変）
    repo.write_ticks("JP225", frame, mode="skip")
    mtime_after = day1.stat().st_mtime_ns

    assert mtime_after == mtime_before


def test_write_ticks_from_csv_path_chunked_yields_same_result(tmp_path):
    import pandas as pd

    from simulator.adapter.repository.tick_parquet import ParquetTickRepository

    frame = _ticks_3days()
    csv_path = tmp_path / "raw.csv"
    frame.to_csv(csv_path, index=False)

    repo = ParquetTickRepository(root=tmp_path)
    # CSV パスからのチャンク取込（メモリ有界）でも frame 取込と同じ結果
    repo.write_ticks("JP225", str(csv_path), mode="overwrite")

    sym = tmp_path / "JP225"
    parts = list(sym.rglob("part.parquet"))
    assert len(parts) == 3
    total_rows = sum(len(pd.read_parquet(p)) for p in parts)
    assert total_rows == len(frame)


def test_write_ticks_csv_chunked_appends_across_chunks_when_day_spans_chunks(tmp_path):
    # 回帰: 1 日が複数 CSV チャンクにまたがる場合（GB級ストリーミングの実態）、
    #       各チャンクの同一日を上書きせず追記して行損失が起きないこと（設計「各日 Parquet 追記」）。
    import pandas as pd

    from simulator.adapter.repository.tick_parquet import ParquetTickRepository

    frame = _ticks_3days()  # 各日 4 tick
    csv_path = tmp_path / "raw.csv"
    frame.to_csv(csv_path, index=False)

    # チャンクサイズ 3 行 → 1 日(4 tick)が 2 チャンクにまたがる
    repo = ParquetTickRepository(root=tmp_path, csv_chunk_rows=3)
    repo.write_ticks("JP225", str(csv_path), mode="overwrite")

    parts = list((tmp_path / "JP225").rglob("part.parquet"))
    total_rows = sum(len(pd.read_parquet(p)) for p in parts)
    assert total_rows == len(frame)  # 行損失なし


def test_write_ticks_translates_io_failure_to_data_error(tmp_path):
    import pytest

    from simulator.adapter.repository.tick_parquet import ParquetTickRepository
    from simulator.domain.exceptions import DataError

    repo = ParquetTickRepository(root=tmp_path)

    # 存在しない CSV パスを与える → 外側 OSError を内側 DataError へ翻訳
    with pytest.raises(DataError):
        repo.write_ticks("JP225", str(tmp_path / "nonexistent.csv"), mode="overwrite")


# =========================================================================
# Section 4: load_ticks（[start,end) 半開・columns 部分取得・空 frame・端日境界）
# =========================================================================

def _ticks_7days():
    """2024-03-01..07 の決定論的 7 日分 tick frame。"""
    from datetime import date

    return generate_ticks(
        TickGenConfig(start_date=date(2024, 3, 1), days=7, ticks_per_day=4)
    )


def _written_repo_7days(tmp_path):
    from simulator.adapter.repository.tick_parquet import ParquetTickRepository

    repo = ParquetTickRepository(root=tmp_path)
    repo.write_ticks("JP225", _ticks_7days(), mode="overwrite")
    return repo


def test_load_ticks_returns_only_rows_within_half_open_range(tmp_path):
    from datetime import datetime

    import pandas as pd

    repo = _written_repo_7days(tmp_path)

    # [D2, D4) = [2024-03-02, 2024-03-04) → D2, D3 のみ。D1 と D4 以降を含まない
    out = repo.load_ticks("JP225", datetime(2024, 3, 2), datetime(2024, 3, 4))

    days = pd.to_datetime(out["timestamp"]).dt.day.unique().tolist()
    assert sorted(days) == [2, 3]
    assert 1 not in days
    assert 4 not in days


def test_load_ticks_columns_subset_returns_only_requested_columns(tmp_path):
    from datetime import datetime

    repo = _written_repo_7days(tmp_path)

    out = repo.load_ticks(
        "JP225",
        datetime(2024, 3, 2),
        datetime(2024, 3, 4),
        columns=["timestamp", "last"],
    )

    assert list(out.columns) == ["timestamp", "last"]


def test_load_ticks_returns_empty_frame_when_no_data_in_range(tmp_path):
    from datetime import datetime

    repo = _written_repo_7days(tmp_path)

    # データ範囲外（2025 年）→ 空 frame（例外でない）
    out = repo.load_ticks("JP225", datetime(2025, 1, 1), datetime(2025, 1, 5))

    assert len(out) == 0


def test_load_ticks_is_deterministic(tmp_path):
    from datetime import datetime

    import pandas as pd

    repo = _written_repo_7days(tmp_path)

    out1 = repo.load_ticks("JP225", datetime(2024, 3, 2), datetime(2024, 3, 5))
    out2 = repo.load_ticks("JP225", datetime(2024, 3, 2), datetime(2024, 3, 5))

    pd.testing.assert_frame_equal(out1.reset_index(drop=True), out2.reset_index(drop=True))


def test_load_ticks_end_at_day_boundary_midnight_does_not_open_end_day(tmp_path):
    from datetime import datetime

    import pandas as pd

    repo = _written_repo_7days(tmp_path)

    # end がちょうど日境界 00:00:00 → end 当日(D4)を一切含まない（半開の境界固定）
    out = repo.load_ticks("JP225", datetime(2024, 3, 1), datetime(2024, 3, 4, 0, 0, 0))

    ts = pd.to_datetime(out["timestamp"])
    assert (ts < datetime(2024, 3, 4)).all()
    assert ts.dt.day.max() == 3  # D4 の 00:00:00 ちょうども含まれない


# =========================================================================
# Section 4b: レビュー指摘 🟡 修正の Red→Green 固定
# =========================================================================

def test_load_ticks_tz_aware_bounds_select_the_same_window_as_naive(tmp_path):
    # 🟡-1 の旧契約は「tz-aware 境界は pandas の生 TypeError になるので DataError へ
    #   翻訳する」だった。ISSUE-402 でこれを**規定ごと撤去**した（症状の翻訳ではなく
    #   原因の除去）。窓境界は Bar / Candle 段と同じ `epoch_seconds` で正規化されるため、
    #   aware は「失敗しない」だけでなく naive と**同一結果**でなければならない。
    #   生例外を漏らさないという元の関心は「aware で例外が出ないこと」で満たされる。
    from datetime import datetime, timezone

    import pandas as pd

    repo = _written_repo_7days(tmp_path)

    aware = repo.load_ticks(
        "JP225",
        datetime(2024, 3, 2, tzinfo=timezone.utc),
        datetime(2024, 3, 4, tzinfo=timezone.utc),
    )
    naive = repo.load_ticks("JP225", datetime(2024, 3, 2), datetime(2024, 3, 4))

    assert len(aware) > 0  # 空一致（両方 0 行）で通る当たりを塞ぐ
    pd.testing.assert_frame_equal(aware, naive)


def test_write_ticks_global_non_monotonic_across_chunks_raises_time_order_error(tmp_path):
    # 🟡-2 回帰: 同一日が複数チャンクに跨り、後チャンクが時刻的に前（大域非単調）の
    #   とき、各チャンク内単調でも非単調 part を生成してはならない。書込確定後に
    #   当該日 part の単調性を検証し TimeOrderError を投げること。
    from datetime import datetime

    import pandas as pd
    import pytest

    from simulator.adapter.repository.tick_parquet import ParquetTickRepository
    from simulator.domain.exceptions import TimeOrderError

    # 同一日(2024-03-01)・大域非単調: [10:00, 10:01] の後に [09:00, 09:01]。
    rows = [
        {"timestamp": datetime(2024, 3, 1, 10, 0), "bid": 1.0, "ask": 1.0, "last": 1.0, "volume": 1},
        {"timestamp": datetime(2024, 3, 1, 10, 1), "bid": 1.0, "ask": 1.0, "last": 1.0, "volume": 1},
        {"timestamp": datetime(2024, 3, 1, 9, 0), "bid": 1.0, "ask": 1.0, "last": 1.0, "volume": 1},
        {"timestamp": datetime(2024, 3, 1, 9, 1), "bid": 1.0, "ask": 1.0, "last": 1.0, "volume": 1},
    ]
    frame = pd.DataFrame(rows)
    csv_path = tmp_path / "raw.csv"
    frame.to_csv(csv_path, index=False)

    # chunksize=2 → 2 チャンク。各チャンク内は単調だが大域は非単調。
    repo = ParquetTickRepository(root=tmp_path, csv_chunk_rows=2)

    with pytest.raises(TimeOrderError):
        repo.write_ticks("JP225", str(csv_path), mode="overwrite")


def test_load_ticks_columns_subset_pushes_down_to_read_parquet(tmp_path, monkeypatch):
    # 🟡-3 回帰: columns 指定時は IO 段（pd.read_parquet の columns 引数）で列
    #   pushdown すること（全列読み→事後 pandas スライスは IO を無駄にする）。
    from datetime import datetime

    import simulator.adapter.repository.tick_parquet as mod

    repo = _written_repo_7days(tmp_path)

    captured: list = []
    real_read_parquet = mod.pd.read_parquet

    def _spy_read_parquet(path, *args, **kwargs):
        captured.append(kwargs.get("columns"))
        return real_read_parquet(path, *args, **kwargs)

    monkeypatch.setattr(mod.pd, "read_parquet", _spy_read_parquet)

    repo.load_ticks(
        "JP225",
        datetime(2024, 3, 2),
        datetime(2024, 3, 4),
        columns=["timestamp", "last"],
    )

    # IO 段の columns pushdown が効いている（read_parquet に columns が渡る）
    assert captured, "read_parquet が呼ばれていない"
    assert all(c == ["timestamp", "last"] for c in captured), captured


def test_write_ticks_skip_multichunk_fresh_write_no_row_loss(tmp_path):
    # 🟡-4 回帰: skip モードでも初回(fresh)書込は opened 管理＋append で行損失しない。
    #   1 日が複数 CSV チャンクに跨る fresh skip 書込で全行が保存されること。
    import pandas as pd

    from simulator.adapter.repository.tick_parquet import ParquetTickRepository

    frame = _ticks_3days()  # 各日 4 tick
    csv_path = tmp_path / "raw.csv"
    frame.to_csv(csv_path, index=False)

    # chunksize=3 → 1 日(4 tick)が 2 チャンクに跨る。skip かつ既存日なし(fresh)。
    repo = ParquetTickRepository(root=tmp_path, csv_chunk_rows=3)
    repo.write_ticks("JP225", str(csv_path), mode="skip")

    parts = list((tmp_path / "JP225").rglob("part.parquet"))
    total_rows = sum(len(pd.read_parquet(p)) for p in parts)
    assert total_rows == len(frame)  # 行損失なし


# =========================================================================
# Section 5: 隔離検証（依存方向違反 0 の回帰ガード）
#   — pyarrow/pandas が usecase 層へ漏出しないことを全 usecase モジュールで固定。
#     本テストは「常に Green を維持する不変条件（回帰ガード）」であり、
#     振る舞いの Red-Green サイクルではない（成功テスト先行ではない）。
# =========================================================================

def _runtime_top_level_imports(module_file: str) -> set[str]:
    """指定モジュールの実行時 import（TYPE_CHECKING 除外）のトップレベル名を返す。"""
    import ast
    import pathlib

    tree = ast.parse(pathlib.Path(module_file).read_text())

    type_checking_nodes: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.If):
            test = node.test
            is_tc = (
                (isinstance(test, ast.Name) and test.id == "TYPE_CHECKING")
                or (isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING")
            )
            if is_tc:
                for child in ast.walk(node):
                    type_checking_nodes.add(id(child))

    mods: set[str] = set()
    for node in ast.walk(tree):
        if id(node) in type_checking_nodes:
            continue
        if isinstance(node, ast.Import):
            for alias in node.names:
                mods.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom) and node.module:
            mods.add(node.module.split(".")[0])
    return mods


def test_no_usecase_module_imports_pandas_or_pyarrow_at_runtime():
    import pathlib

    import simulator.usecase as usecase_pkg

    usecase_dir = pathlib.Path(usecase_pkg.__file__).parent
    offenders: dict[str, set[str]] = {}
    for py in usecase_dir.glob("*.py"):
        mods = _runtime_top_level_imports(str(py))
        leaked = mods & {"pandas", "pyarrow"}
        if leaked:
            offenders[py.name] = leaked

    assert offenders == {}, f"usecase 層に pandas/pyarrow 漏出: {offenders}"
