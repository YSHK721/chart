"""A-EaRegistrySeriesCatalog: ea_name → 指標レジストリの登録系列名（E-3・§12.5）。

**単一ソース**: `simulator.main._EA_FACTORIES`（および未登録時のフォールバック先
`_factory_tc24051901`）を**実際に呼んで**登録系列を得る。ea_name → 系列名の対応表を
本モジュールに書き写さない。§12.1 が「戦略ごとの明示指定リストのハードコード」を
禁じているのに加え、書き写した表は登録表が増えた時に必ず取り残される
（本リポジトリで繰り返し起きている壊れ方）。

探索方法: 各 factory は自分でデータファイルを読む（`_load_dataframe` = comma /
`_load_mt5_dataframe` = タブ区切り）。どちらを読むかは factory 側の知識なので、
**両形式の最小サンプルを書いて順に試し、成功した方を採る**。数行の DataFrame で足りる
（必要なのは登録された系列名の集合だけで、値は使わない）。

系列名の取り出しは `PandasIndicatorRegistry` の**公開されたエラー契約**を使う。
未登録名を `get` すると `IndicatorBufferError` が
``context={"name": ..., "available": [...]}`` を伴って送出される。私有属性
（``_series``）を覗かずに済む。

fail-safe: 探索に失敗したら空集合を返す（＝sizing 不可として受付時に拒否される）。
黙って通して誤った発注量で走らせるより、拒否して気付かせる。
"""
from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

from simulator.sim_ui.usecase.job_ports import IndicatorSeriesCatalogPort

# 探索用の最小サンプル。指標の warmup を満たす程度の行数があればよい（値は使わない）。
_ROWS = 12
_PROBE_NAME = "__sim_ui_probe_missing_series__"


def _comma_csv() -> str:
    """`_load_dataframe`（`pd.read_csv`）が読む comma 形式の最小サンプル。"""
    head = "time,open,high,low,close,volume\n"
    rows = "".join(
        f"2024-01-{i + 1:02d}T00:00:00,{100 + i},{101 + i},{99 + i},{100 + i},1\n"
        for i in range(_ROWS)
    )
    return head + rows


def _mt5_tsv() -> str:
    """`_load_mt5_dataframe`（`pd.read_csv(sep="\\t")`）が読む MT5 形式の最小サンプル。"""
    head = "<DATE>\t<TIME>\t<OPEN>\t<HIGH>\t<LOW>\t<CLOSE>\t<TICKVOL>\t<VOL>\t<SPREAD>\n"
    rows = "".join(
        f"2024.01.{i + 1:02d}\t00:00:00\t{100 + i}\t{101 + i}\t{99 + i}\t{100 + i}\t1\t0\t2\n"
        for i in range(_ROWS)
    )
    return head + rows


class EaRegistrySeriesCatalog(IndicatorSeriesCatalogPort):
    """`simulator.main` の EA ファクトリ登録表を単一ソースとする系列カタログ。"""

    def __init__(self) -> None:
        self._cache: "dict[str, frozenset[str]]" = {}

    def series_for(self, ea_name: str) -> "frozenset[str]":
        """登録系列名の集合を返す。探索できないときは空集合（fail-safe）。"""
        if ea_name in self._cache:
            return self._cache[ea_name]
        try:
            series = self._probe(ea_name)
        except Exception:
            series = frozenset()
        self._cache[ea_name] = series
        return series

    # --- 探索本体（検定から差し替えられるよう独立させる）------------------

    def _probe(self, ea_name: str) -> "frozenset[str]":
        """`_EA_FACTORIES` の該当ファクトリを実際に呼び、registry の系列名を得る。"""
        # 遅延 import: 本モジュールの import 時点で pandas 一式を引き込まない。
        from simulator.main import _EA_FACTORIES, _factory_tc24051901

        factory = _EA_FACTORIES.get(ea_name, _factory_tc24051901)
        with tempfile.TemporaryDirectory(prefix="sim_ui_series_probe_") as tmp:
            root = Path(tmp)
            candidates = (
                _write(root / "probe.mt5.csv", _mt5_tsv()),
                _write(root / "probe.csv", _comma_csv()),
            )
            last_error: "Exception | None" = None
            for data_path in candidates:
                try:
                    _strategy, registry, _repo = factory(_context(data_path))
                except Exception as exc:  # この形式では読めない factory → 次を試す
                    last_error = exc
                    continue
                return _series_names(registry)
        raise RuntimeError(
            f"{ea_name} の指標レジストリを探索できませんでした: {last_error!r}"
        )


def _write(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    return path


def _context(data_path: Path) -> Any:
    """探索用の `_EaBuildContext`。指標の周期は最小限（値は使わないため）。"""
    from simulator.main import _EaBuildContext

    return _EaBuildContext(
        data_path=data_path,
        ma_period=2,
        ma_method="sma",
        adx_period=2,
        weekly_forecast=None,
        weekly_p_tp=0.5,
        weekly_capital=0.0,
        weekly_f_risk=0.01,
    )


def _series_names(registry: Any) -> "frozenset[str]":
    """registry の登録系列名を公開エラー契約（`available`）から取り出す。"""
    from simulator.domain.exceptions import IndicatorBufferError

    try:
        registry.get(_PROBE_NAME)
    except IndicatorBufferError as exc:
        return frozenset(exc.context.get("available", ()))
    raise RuntimeError("未登録系列の参照が IndicatorBufferError にならなかった")
