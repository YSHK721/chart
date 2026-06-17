"""結果永続化アダプタ（ResultSinkPort 実装）。

JsonResultRepository / ParquetResultRepository。save_trades / save_stats / save_report の
3 操作を提供する。I/O 例外（OSError 等）は BacktestError へ翻訳し context を付与する
（CLEAN_ARCH §6・外側例外の内側漏出禁止）。

stats(JSON) / report(HTML テキスト) の永続化は両 Repository で共通のため _BaseResultRepository
に集約し、差分（trades の出力形式）のみを各サブクラスで実装する（DRY / SRP）。

adapter 層は usecase + domain + 技術ドライバ（pandas/pyarrow）のみに依存する。
"""
from __future__ import annotations

import abc
import json
from typing import Any

from backtest.domain.exceptions import BacktestError
from backtest.usecase.ports import ResultSinkPort


def _wrap_io(op: str, path: Any, fn) -> None:
    """I/O 操作を実行し、失敗時は BacktestError(context 付与) へ翻訳する。"""
    try:
        fn()
    except Exception as exc:  # OSError / pandas / pyarrow 等を内側へ翻訳
        raise BacktestError(
            f"{op} の保存に失敗しました: {path}",
            context={"op": op, "path": str(path), "cause": repr(exc)},
        ) from exc


class _BaseResultRepository(ResultSinkPort):
    """stats(JSON) / report(HTML) の永続化を共通実装する基底。

    save_trades のみ trades の出力形式が Repository ごとに異なるため abstract に残す。
    """

    @abc.abstractmethod
    def save_trades(self, df: Any, path: Any) -> None:
        raise NotImplementedError

    def save_stats(self, stats: dict, path: Any) -> None:
        def _write() -> None:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(stats, f, ensure_ascii=False)

        _wrap_io("save_stats", path, _write)

    def save_report(self, html: str, path: Any) -> None:
        def _write() -> None:
            with open(path, "w", encoding="utf-8") as f:
                f.write(html)

        _wrap_io("save_report", path, _write)


class JsonResultRepository(_BaseResultRepository):
    """trades=JSON / stats=JSON / report=HTML テキストで永続化する。"""

    def save_trades(self, df: Any, path: Any) -> None:
        _wrap_io("save_trades", path, lambda: df.to_json(path))


class ParquetResultRepository(_BaseResultRepository):
    """trades=parquet / stats=JSON / report=HTML テキストで永続化する。"""

    def save_trades(self, df: Any, path: Any) -> None:
        _wrap_io("save_trades", path, lambda: df.to_parquet(path))
