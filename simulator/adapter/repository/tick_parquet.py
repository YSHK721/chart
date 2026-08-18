"""ParquetTickRepository: 本番 tick-store の入出力アダプタ（TickDataPort / TickStorePort 実装）。

レイアウト（hive partition）:
    <root>/<symbol>/year=YYYY/month=MM/day=DD/part.parquet

思想（ユーザー承認の 2 段分離）:
    raw landing（取得物を不変アーカイブ）→ 振り分け変換（raw を日付ルーティングし
    Y/M/D Parquet を生成）。write_ticks は in-memory frame だけでなく CSV パス
    （大容量）からも取り込み、チャンク/ストリーミングで日付 groupby ルーティングする
    ことでメモリを有界に保つ（一括 RAM ロード禁止）。

技術隔離: pyarrow / pandas は本ファイル内に閉じる（CLEAN_ARCH §6）。
例外翻訳: pyarrow/pandas/OSError → DataError・列欠損 → MissingBarError・非昇順 → TimeOrderError。

窓境界の規則（ISSUE-402）: `load_ticks` の `[start, end)` は Bar / Candle 段と**同一の
実体**（`simulator.domain.bar_time.epoch_seconds` /
`datawindow.half_open.HalfOpenEpochWindow.contains`）で解釈する。Tick 段に固有の窓規則は
持たない。行動固定は `simulator/tests/unit/test_tick_window_single_source.py`。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd

# 窓境界の正規化・半開判定は中立共有パッケージ／domain の**同一オブジェクト**を読む
# （Bar 段 `windowed_market_data.py` と同じ 2 行。Tick 段で書き直せば複製になる）。
from datawindow.half_open import HalfOpenEpochWindow
from simulator.adapter.repository._tick_frame import (
    TICK_COLUMNS,
    timestamp_epoch_seconds,
    validate_tick_columns,
    with_partition_columns,
)
from simulator.domain.bar_time import epoch_seconds
from simulator.domain.exceptions import DataError, TimeOrderError
from simulator.usecase.ports import TickDataPort, TickStorePort

# 大容量 CSV をチャンク読みするときの 1 チャンクの行数（メモリ有界化）。
_CSV_CHUNK_ROWS = 500_000


class TickWriteResult:
    """write_ticks の結果（書き込んだ日数・行数）。"""

    def __init__(self, days_written: int, rows_written: int) -> None:
        self.days_written = days_written
        self.rows_written = rows_written


class ParquetTickRepository(TickDataPort, TickStorePort):
    """parquet hive layout で tick を読み書きする TickDataPort / TickStorePort 実装。"""

    def __init__(self, root: Any, csv_chunk_rows: int = _CSV_CHUNK_ROWS) -> None:
        self._root = Path(root)
        self._csv_chunk_rows = csv_chunk_rows

    # ---- TickStorePort ----

    def write_ticks(
        self, symbol: str, frame_or_csv: Any, mode: str = "overwrite"
    ) -> TickWriteResult:
        """raw（frame または CSV パス）を日別 Parquet へ振り分け書き込みする（冪等）。"""
        rows_written = 0
        # この write 呼び出し内で既に開いた（=本 call で初回書込済の）日。
        # 初回は overwrite（前回 call の残骸を再生成）、2 回目以降は append し、
        # 1 日が複数チャンクにまたがっても行損失しないようにする（設計「各日 Parquet 追記」）。
        opened: set[Path] = set()
        for chunk in self._iter_raw_chunks(frame_or_csv):
            validate_tick_columns(chunk)
            partitioned = with_partition_columns(chunk)
            for (year, month, day), group in partitioned.groupby(
                ["year", "month", "day"], sort=True
            ):
                part_path = self._part_path(symbol, int(year), int(month), int(day))
                if mode == "skip" and part_path not in opened and part_path.exists():
                    continue
                append = part_path in opened
                self._write_day(part_path, group, append=append)
                opened.add(part_path)
                rows_written += len(group)
        return TickWriteResult(days_written=len(opened), rows_written=rows_written)

    # ---- TickDataPort ----

    def load_ticks(
        self, symbol: str, start: Any, end: Any, columns: Any = None
    ) -> pd.DataFrame:
        """[start, end) 半開区間の tick を返す（2 段: partition プルーニング + timestamp 厳密フィルタ）。

        該当なしは空 frame（例外でない）。columns 指定時は当該列のみ返す
        （IO 段で read_parquet に columns を渡し列 pushdown する）。

        窓境界の解釈（ISSUE-402 の是正・Bar / Candle 段と**同一規則**）:
            境界 ``start`` / ``end`` は `simulator.domain.bar_time.epoch_seconds` で
            epoch 秒へ正規化し、半開判定は
            `datawindow.half_open.HalfOpenEpochWindow.contains` に委ねる。したがって
            受理する時刻表現は `bar.time` / 取得窓と同じ（epoch int / aware datetime /
            naive datetime（= UTC）/ ``numpy.datetime64``）であり、Tick 段だけが別の
            規則を持つことはない。是正前は naive ``pandas.Timestamp`` だけが成立し、
            aware は ``TypeError('Invalid comparison between dtype=datetime64[us] and
            Timestamp')``、epoch int は ``AttributeError("'int' object has no attribute
            'year'")`` で、いずれも ``DataError`` へ翻訳されて失敗していた（是正前の
            実測。再現は `simulator/tests/unit/test_tick_window_single_source.py`）。
            未対応の表現は `epoch_seconds` の契約どおり ``ConfigError``（`DataError` へ
            包み直さない。データの不良ではなく指定の不良である）。

        保存 timestamp の解釈:
            保存列は naive UTC が契約（`tools/ingest_ticks.to_canonical_ticks` が tz を
            剥がす）。比較前に ``to_datetime(..., utc=True)`` → ``tz_localize(None)`` →
            ``astype("datetime64[s]")`` で epoch 秒へ落とす。naive を UTC とみなす点は
            窓境界の規則と同一であり、保存列が tz-aware であっても同じ UTC epoch に
            なる（実測: aware 列に ``astype("datetime64[s]")`` を直接当てると pandas が
            ``TypeError`` を出す。tz の有無で結果が変わる式は使わない）。dtype の解像度
            （us / ns）にも依存しない＝``astype("int64") // 1_000_000_000`` のような
            ns 前提の式は使わない。

        粒度についての実測と限界（推測しない）:
            窓境界が**整数秒**である限り、本実装の判定は是正前の pandas 直接比較と
            集合として一致する（整数 B と実時刻 t に対し floor(t) >= B ⟺ t >= B、
            floor(t) < B ⟺ t < B）。現存する全呼出（`main._bar_period` の
            ``bar.time (+60s)``、`tools/run_scan_contacts_cli` の
            ``pd.Timestamp(int, unit="s")``）は整数秒である。境界に秒未満成分を与えた
            場合は秒へ floor される＝Bar / Candle 段と同じ秒粒度になる。

        空窓 ``start >= end`` の扱い（是正で変わった唯一の点・実測）:
            `_date_predicate` が空リストを返すため part を 1 つも読まない。是正前は
            ``start == end`` が日境界**以外**のとき当該日の part を読んでから全行を
            落としていた（返り値は 0 行だが parquet 由来の dtype を持っていた）。是正後は
            「データなし」枝と同じ 0 行 frame（列は TICK_COLUMNS・dtype は object）を返す。
            行数・列名は不変、dtype のみ変わる。現存する呼出は空窓を作らない
            （`main._bar_period` は最小でも end = start + 60s、
            `tools/run_scan_contacts_cli` はバー区間を渡す）。

        到達可能性（実測・2026-08-18。断定と未検証を分ける）:
            実測 1: 本メソッドの非テスト呼出は 2 箇所である
                （``grep -rn "load_ticks" --include=*.py`` / worktree 除外）。
                  - `simulator/tools/run_scan_contacts_cli.py`
                    （``if __name__ == "__main__"`` を持つ実行可能 CLI。
                    ``pd.Timestamp(int, unit="s")`` = naive Timestamp を渡す）
                  - `simulator/main/_build_real_tick_model`
                    （`build_interactor` が `tick_model` に real ticks を要求するときのみ）
            実測 2: ``grep -rn "EngineBinding("`` の非 worktree 検索は
                `simulator/tests/tester_settings_engine_fixtures.py:118` の 1 件のみ。
                すなわち `EngineBinding` を構築する非テストコードは存在しない。
            未検証: 実測 2 は「`EngineBinding` 経由の到達がない」ことしか示さない。
                `_build_real_tick_model` は `build_interactor(tick_store_root=...,
                config_overrides=...)` からも到達でき、その 2 キーは
                `tools/walk_forward_cli.py` の受理キーワード集合に含まれる。よって
                「本メソッドは本番未到達」と**断定はできない**（実際に走らせた
                運用実績の有無は本タスクで測っていない）。
            したがって本是正の目的は「規則の非対称を消すこと」であり、既知の稼働経路
            （naive Timestamp）は測定上不変（是正前後で 952k 行が完全一致）である。
        """
        from simulator.adapter.repository._tick_frame import _date_predicate

        empty_cols = list(columns) if columns is not None else list(TICK_COLUMNS)

        # columns 指定時は timestamp フィルタに必要な timestamp 列を必ず含めて
        # IO 段の列 pushdown を効かせる（全列読み→事後スライスは IO を浪費する）。
        read_columns = None
        if columns is not None:
            req = list(columns)
            read_columns = req if "timestamp" in req else ["timestamp", *req]

        # 境界の正規化は try の外に置く。未対応の時刻表現は `epoch_seconds` が投げる
        # ``ConfigError`` のまま伝播させる（Bar 段 `WindowedMarketDataRepository.load`
        # と対称。IO 失敗を表す `DataError` へ包むと原因の種類が消える）。
        window = HalfOpenEpochWindow(epoch_seconds(start), epoch_seconds(end))

        try:
            # 第 1 段: partition プルーニング — [start,end) を覆う日の part.parquet のみ読む。
            wanted_days = _date_predicate(window.start, window.end)
            frames: list[pd.DataFrame] = []
            for year, month, day in wanted_days:
                part_path = self._part_path(symbol, year, month, day)
                if not part_path.exists():
                    continue
                frames.append(pd.read_parquet(part_path, columns=read_columns))

            if not frames:
                return pd.DataFrame(columns=empty_cols)

            df = pd.concat(frames, ignore_index=True)

            # 第 2 段: timestamp 厳密フィルタ — 半開判定は共有実体 `contains` に委ねる
            # （述語をここへ書き直すと Bar / Candle 段との複製になる）。
            # コスト実測（20 日 × 47.6k = 952k 行を 1 回 load・同一機・2 回計測）:
            #   是正前（pandas 直接比較） 0.109 / 0.119 s
            #   是正後（map(contains)）   0.281 / 0.288 s   差 +0.17 s
            # load_ticks は 1 run につき 1 回であり、後続の 952k tick 走査に対して
            # 支配的でない。返り値の frame は 952k 行で是正前と完全一致（実測）。
            # timestamp → epoch 秒は共有実体 `timestamp_epoch_seconds` に委ねる
            # （ISSUE-406: CLI 側の手書き複製が ns 前提で 10^6 倍ずれた。実体は 1 つ）。
            ts_epoch = timestamp_epoch_seconds(df["timestamp"])
            df = df.loc[ts_epoch.map(window.contains)].reset_index(drop=True)
        except DataError:
            raise
        except Exception as exc:  # pyarrow / pandas / OSError 等の IO 失敗を翻訳
            # 「tz-aware 境界の比較 TypeError」はここへ来ない（ISSUE-402 で原因を除去。
            # 境界は epoch 秒へ正規化済みであり、保存列との tz 整合は問題にならない）。
            raise DataError(
                f"parquet の読み込みに失敗しました: {symbol}",
                context={"symbol": symbol, "cause": repr(exc)},
            ) from exc

        if columns is not None:
            df = df[list(columns)]
        return df

    # ---- internal ----

    def _iter_raw_chunks(self, frame_or_csv: Any):
        """frame はそのまま、CSV パスはチャンク列で yield する（メモリ有界）。"""
        if isinstance(frame_or_csv, pd.DataFrame):
            yield frame_or_csv
            return
        try:
            reader = pd.read_csv(
                frame_or_csv, chunksize=self._csv_chunk_rows, parse_dates=["timestamp"]
            )
            for chunk in reader:
                yield chunk
        except Exception as exc:  # pandas / OSError 等を内側へ翻訳
            raise DataError(
                f"CSV の読み込みに失敗しました: {frame_or_csv}",
                context={"source": str(frame_or_csv), "cause": repr(exc)},
            ) from exc

    def _part_path(self, symbol: str, year: int, month: int, day: int) -> Path:
        return (
            self._root
            / symbol
            / f"year={year:04d}"
            / f"month={month:02d}"
            / f"day={day:02d}"
            / "part.parquet"
        )

    def _write_day(
        self, part_path: Path, group: pd.DataFrame, *, append: bool
    ) -> None:
        """1 日分の tick を part.parquet へ書き込む（TICK_COLUMNS のみ）。

        append=True のとき既存 part に追記する（1 日が複数チャンクにまたがる場合）。
        Parquet は in-place 追記できないため既存を読み concat して再書込する
        （連結量は「1 日分」に有界＝総量に比例しない）。

        validate_tick_columns はチャンク単位の検証のため、同一日が複数チャンクに
        跨り後チャンクが時刻的に前（大域非単調）でも各チャンク内単調なら素通りする。
        書込確定値（既存 append 後の out）の timestamp 単調性をここで再検証し、
        非単調 part の生成を防ぐ（TimeOrderError へ翻訳）。
        """
        try:
            part_path.parent.mkdir(parents=True, exist_ok=True)
            out = group[list(TICK_COLUMNS)]
            if append and part_path.exists():
                existing = pd.read_parquet(part_path)
                out = pd.concat([existing, out], ignore_index=True)
        except Exception as exc:  # pyarrow / pandas / OSError 等を内側へ翻訳
            raise DataError(
                f"parquet の書き込みに失敗しました: {part_path}",
                context={"path": str(part_path), "cause": repr(exc)},
            ) from exc

        # 大域単調性ガード（チャンク跨ぎ非単調の検出）— 書込前に検証する。
        out_ts = pd.to_datetime(out["timestamp"])
        if not out_ts.is_monotonic_increasing:
            raise TimeOrderError(
                "timestamp が昇順ではありません（チャンク跨ぎ非単調）",
                context={
                    "path": str(part_path),
                    "first": str(out_ts.iloc[0]),
                    "last": str(out_ts.iloc[-1]),
                },
            )

        try:
            out.to_parquet(part_path, index=False)
        except Exception as exc:  # pyarrow / pandas / OSError 等を内側へ翻訳
            raise DataError(
                f"parquet の書き込みに失敗しました: {part_path}",
                context={"path": str(part_path), "cause": repr(exc)},
            ) from exc
