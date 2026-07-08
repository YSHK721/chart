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
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd

from simulator.adapter.repository._tick_frame import (
    TICK_COLUMNS,
    validate_tick_columns,
    with_partition_columns,
)
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

        tz 方針: 保存 timestamp は naive UTC 固定（synth_ticks 由来）。tz-aware の
        start/end を与えると naive 値との比較で pandas が TypeError を投げるため、
        当該比較も含め DataError へ翻訳する（境界での生例外漏出を防止）。
        """
        from simulator.adapter.repository._tick_frame import _date_predicate

        empty_cols = list(columns) if columns is not None else list(TICK_COLUMNS)

        # columns 指定時は timestamp フィルタに必要な timestamp 列を必ず含めて
        # IO 段の列 pushdown を効かせる（全列読み→事後スライスは IO を浪費する）。
        read_columns = None
        if columns is not None:
            req = list(columns)
            read_columns = req if "timestamp" in req else ["timestamp", *req]

        try:
            # 第 1 段: partition プルーニング — [start,end) を覆う日の part.parquet のみ読む。
            wanted_days = _date_predicate(start, end)
            frames: list[pd.DataFrame] = []
            for year, month, day in wanted_days:
                part_path = self._part_path(symbol, year, month, day)
                if not part_path.exists():
                    continue
                frames.append(pd.read_parquet(part_path, columns=read_columns))

            if not frames:
                return pd.DataFrame(columns=empty_cols)

            df = pd.concat(frames, ignore_index=True)

            # 第 2 段: timestamp 厳密フィルタ — [start, end) 半開（end 当日 00:00:00 を開かない）。
            # 保存 timestamp は naive UTC 固定（synth_ticks 由来）。tz-aware の
            # start/end が与えられると pandas が生 TypeError を投げるため、この
            # 比較も try 内に置き DataError へ翻訳する（境界の例外漏出防止）。
            ts = pd.to_datetime(df["timestamp"])
            start_ts = pd.Timestamp(start)
            end_ts = pd.Timestamp(end)
            mask = (ts >= start_ts) & (ts < end_ts)
            df = df.loc[mask].reset_index(drop=True)
        except DataError:
            raise
        except Exception as exc:  # pyarrow / pandas / OSError / tz 比較 TypeError 等を翻訳
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
