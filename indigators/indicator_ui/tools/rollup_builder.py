"""rollup_builder — 上位足の増分ロールアップ（marketdata.rollup への薄い委譲・enabler③）。

ロールアップ生成ロジック（``RollupState`` / ``stream_build`` / ``incremental_update`` /
``_RollupWriter`` / ``_rollup_path`` / ``merge_same_period`` 等）は :mod:`marketdata.rollup` へ
物理移設した（規則源は :func:`marketdata.resample.resample_ohlc`・再実装禁止・§4）。

本モジュールは既存 import 元（``import rollup_builder as rb`` / ``export_jp225_m1`` の局所 import・
``tools/tests/test_rollup_builder.py``）の後方互換のため、:mod:`marketdata.rollup` を**そのまま**
本モジュール名で再公開する。``rollup_builder`` への属性アクセス・monkeypatch は
:mod:`marketdata.rollup` の同一オブジェクトへ透過する（モジュール別名化）。

委譲方式（重要）: ``sys.modules['rollup_builder']`` を :mod:`marketdata.rollup` の**同一モジュール
オブジェクト**へ差し替える。これにより ``rb.stream_build`` / ``rb.pd`` /
``rb._INCREMENTAL_TAIL_PROBE_ROWS`` 等の属性参照・monkeypatch が移設先の実体へ直接届く
（再エクスポートのコピーでは module 定数の monkeypatch が効かないため・回帰防止）。
"""

from __future__ import annotations

import sys

from marketdata import rollup as _rollup

# 既存呼出元が ``import rollup_builder as rb`` でアクセスする名前を移設先の同一実体へ束ねる。
# sys.modules を marketdata.rollup の同一モジュールオブジェクトへ差し替えることで、属性参照・
# monkeypatch（pd.read_csv / _INCREMENTAL_TAIL_PROBE_ROWS 等）が移設先の実体へ透過する。
sys.modules[__name__] = _rollup
