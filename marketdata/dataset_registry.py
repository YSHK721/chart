"""dataset_registry — datasetRef 記述子レジストリ（単一 dict）の唯一源（ISSUE-094 🟡-9）。

datasetRef ごとの属性（実 CSV パス・外れ値クランプ対象か・ロールアップ経路か・ティック由来か）を
**1 つの記述子レジストリ** に集約する。従来は同じ ref 台帳が 4 箇所に断片化していた:
  - ``dataset.DATASET_WHITELIST``      : ref → 実 CSV パス
  - ``dataset._OUTLIER_CLAMP_REFS_SET``: 読取時クランプ対象 ref 集合
  - ``dataset._ROLLUP_REFS``           : ロールアップ経路 ref
  - ``tf_meta.TICK_REFS``              : 形成中バー/tf-period 供給 ref（ティック由来）
新銘柄追加時に 4 箇所の整合が必要だった（同一アクター＝ref 台帳所有者の 4 分割）。本レジストリを
唯一源とし、上記 4 つの公開/内部名は **導出値** として各モジュールで温存する（利用側は無変更）。

依存方向: 本モジュールは :mod:`marketdata.paths`（DATA_DIR 単一基点）のみに依存する。
:mod:`marketdata.dataset` と :mod:`marketdata.tf_meta` が本モジュールを参照する（逆は無い・
循環禁止）。中立な最下層 peer に置くことで tf_meta↔dataset の相互依存を発生させない。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from marketdata.paths import DATA_DIR

# workspace ルート（このファイル: marketdata/ → parents[1] = /workspaces/app）。sample の
# 同梱 CSV 解決に使う（時系列データ本体は DATA_DIR 配下）。
_WORKSPACE_ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class DatasetDescriptor:
    """datasetRef の属性記述子（1 ref = 1 記述子）。

    Attributes:
        path: ホワイトリスト解決先の実 CSV パス（生パス直送・パストラバーサル防止の唯一解決）。
        clamp_outliers: 読取時 外れ値クランプ（serving 戦略）の対象か（実市場 ref のみ True）。
        rollup: 1 分足原子＋事前生成ロールアップ CSV の供給経路を使うか（メモリ有界化・D-2）。
        tick: 形成中バー/tf-period を供給するティック由来 ref か（ticks parquet を持つ）。
    """

    path: Path
    clamp_outliers: bool = False
    rollup: bool = False
    tick: bool = False


# datasetRef 記述子レジストリ（唯一源）。挿入順は従来の DATASET_WHITELIST と一致させる。
REGISTRY: dict[str, DatasetDescriptor] = {
    # サンプル（同梱・日足 OHLCV）。合成 golden のためクランプ対象外。
    "sample": DatasetDescriptor(
        path=_WORKSPACE_ROOT
        / "lightweight-charts-python-main"
        / "examples"
        / "4_line_indicators"
        / "ohlcv.csv",
    ),
    # JP225 日足（Dukascopy E_N225Jap・外れ値補正済み）。実市場ゆえクランプ対象。
    "jp225": DatasetDescriptor(path=DATA_DIR / "jp225_daily.csv", clamp_outliers=True),
    # JP225 1分足原子（全時間足はこれを resample）。実市場・ロールアップ経路。
    "jp225_m1": DatasetDescriptor(
        path=DATA_DIR / "jp225_m1.csv", clamp_outliers=True, rollup=True
    ),
    # JP225 1分足（ティック由来・原子）。実市場・ロールアップ経路・ティック由来供給。
    "jp225_tick": DatasetDescriptor(
        path=DATA_DIR / "jp225_tick_m1.csv",
        clamp_outliers=True,
        rollup=True,
        tick=True,
    ),
}


def whitelist() -> "dict[str, Path]":
    """ref → 実 CSV パスの新規 mutable dict を導出する（DATASET_WHITELIST の源）。

    monkeypatch.setitem で一時 ref を追加できるよう、レジストリを共有せず毎回新規 dict を返す。
    """
    return {ref: d.path for ref, d in REGISTRY.items()}


def clamp_refs() -> "dict[str, bool]":
    """外れ値クランプ対象 ref → True の新規 mutable dict を導出する（_OUTLIER_CLAMP_REFS_SET の源）。"""
    return {ref: True for ref, d in REGISTRY.items() if d.clamp_outliers}


def rollup_refs() -> "tuple[str, ...]":
    """ロールアップ経路 ref のタプルを導出する（_ROLLUP_REFS の源・挿入順）。"""
    return tuple(ref for ref, d in REGISTRY.items() if d.rollup)


def tick_refs() -> "frozenset[str]":
    """ティック由来 ref の frozenset を導出する（tf_meta.TICK_REFS の源）。"""
    return frozenset(ref for ref, d in REGISTRY.items() if d.tick)


__all__ = [
    "DatasetDescriptor",
    "REGISTRY",
    "whitelist",
    "clamp_refs",
    "rollup_refs",
    "tick_refs",
]
