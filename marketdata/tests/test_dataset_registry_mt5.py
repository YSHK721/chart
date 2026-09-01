"""jp225_mt5（MT5 実時間ティック供給）の datasetRef 記述子の検定（ISSUE-447 段階 1・A-1）。

設計入力（唯一の仕様源）: .doc/MT5_REALTIME_TICK_SUPPLY_BASIC_DESIGN.md
  （設計書はバッククォートで囲まない。宣言整合検定 .claude/scripts/declaration_integrity.py は
   索引に .py と .js しか載せないため、`*.md` を名指すと必ず「存在しない」と判定される。
   囲まなければ検定対象の記号にならず、検定が保証できない主張を立てずに済む。）
  §9 承認表 A-1（**承認**・2026-09-01 依頼者裁定）:
    「`dataset_registry.py` へ `jp225_mt5` 1 エントリ追加。`tick=False` で足内更新経路に
      触れない。1 行削除で可逆」
  §5 記憶域の増分構造 L96-97: 表示面の実体は `<DATA_DIR>/jp225_mt5_m1.csv` と
    `rollups/jp225_mt5/…`（＝ロールアップ経路 ref）。
  §7 H8: `/candles?datasetRef=jp225_mt5` が 200 を返す（A-1 承認後）。

なぜ「tick=False」を**負の表明**として固定するか:
  `tick=True` は `tf_meta.TICK_REFS` へ入り、形成中バー（足内更新）/tf-period 供給経路が
  この ref に対して起動する。MT5 経路の足内更新は §9 A-6 で「未裁定（別段階）」であり、
  本段階の承認条件そのものが「その経路に触れないこと」である。よって
  「TICK_REFS に jp225_mt5 が**入っていない**」を機械的に固定する。承認条件を満たさなく
  なった瞬間（誰かが tick=True にした瞬間）に赤にするのが本検定の役目である。
"""

from __future__ import annotations

import builtins
import pathlib

from marketdata import dataset, dataset_registry, tf_meta
from marketdata.paths import DATA_DIR

_MT5_REF = "jp225_mt5"


# --- A-1: 記述子が引ける（設計 §5・§9） ----------------------------------- #
def test_jp225_mt5の記述子が引ける():
    """ref → 記述子が台帳から解決できる（未登録なら KeyError で赤）。"""
    # Arrange / Act
    d = dataset_registry.REGISTRY[_MT5_REF]

    # Assert（設計 §5 L96: 表示面の実体は <DATA_DIR>/jp225_mt5_m1.csv）
    assert d.path == DATA_DIR / "jp225_mt5_m1.csv"
    assert d.symbol == "JP225"


def test_jp225_mt5はロールアップ経路である():
    """設計 §5 L97 `rollups/jp225_mt5/…`＝上位足は事前生成ロールアップから読む。"""
    assert dataset_registry.REGISTRY[_MT5_REF].rollup is True
    assert _MT5_REF in dataset_registry.rollup_refs()


def test_jp225_mt5は足内更新経路に触れない():
    """A-1 承認条件の本体（`tick=False`）。`TICK_REFS` に入らないことを**負で**固定する。

    A-6（足内更新の MT5 対応）は未裁定＝別段階。ここが True に変わると、承認されていない
    経路（forming_bar / tf-period 供給）が無言で起動する。
    """
    assert dataset_registry.REGISTRY[_MT5_REF].tick is False
    assert _MT5_REF not in dataset_registry.tick_refs()
    assert _MT5_REF not in tf_meta.TICK_REFS


def test_jp225_mt5は実市場refゆえクランプ対象である():
    """`dataset_registry.py:39` の規則「実市場 ref のみ True」に従う（jp225_tick と同格）。

    裁定（2026-09-01 依頼者・追認）: 設計 §9 A-1 が固定するのは `tick=False` のみであり
    `clamp_outliers` は承認文の射程外であったが、上記の台帳既存規則と jp225_tick との
    同格性を根拠に **True を維持**すると裁定された（本行がその判断の記録である）。

    MT5 由来 JP225 は実市場・ティック由来 M1 であり、素材の性質は `jp225_tick` と同一。
    クランプの実体は `dataset._clamp_outlier_bars`（`dataset.py:62-86` 実読）で、±30%
    エンベロープの純粋関数（marketdata/outlier_policy.py）へ委譲するだけであり、
    事前生成物・統計ファイル等の外部生成物を要求しない。
    """
    assert dataset_registry.REGISTRY[_MT5_REF].clamp_outliers is True
    assert dataset._OUTLIER_CLAMP_REFS_SET.get(_MT5_REF) is True


def test_jp225_mt5が4台帳すべてへ整合的に導出される():
    """単一源からの導出（whitelist / clamp / rollup / tick）が食い違わない。"""
    assert dataset.DATASET_WHITELIST[_MT5_REF] == DATA_DIR / "jp225_mt5_m1.csv"
    assert dataset.DATASET_WHITELIST == dataset_registry.whitelist()
    assert dataset._OUTLIER_CLAMP_REFS_SET == dataset_registry.clamp_refs()
    assert dataset._ROLLUP_REFS == dataset_registry.rollup_refs()
    assert tf_meta.TICK_REFS == dataset_registry.tick_refs()


# --- 既存エントリ無改変（追加が既存を動かしていないことの壁） --------------- #
def test_既存refの記述子は1バイトも動いていない():
    """A-1 は「1 エントリ**追加**」であり既存の書き換えではない（可逆性の担保）。"""
    r = dataset_registry.REGISTRY
    assert r["jp225"].path == DATA_DIR / "jp225_daily.csv"
    assert (r["jp225"].rollup, r["jp225"].tick) == (False, False)
    assert r["jp225_m1"].path == DATA_DIR / "jp225_m1.csv"
    assert (r["jp225_m1"].rollup, r["jp225_m1"].tick) == (True, False)
    assert r["jp225_tick"].path == DATA_DIR / "jp225_tick_m1.csv"
    assert (r["jp225_tick"].rollup, r["jp225_tick"].tick) == (True, True)
    assert r["sample"].path.name == "ohlcv.csv"
    # ティック由来は依然 jp225_tick ただ 1 つ（MT5 追加で増えていない）。
    assert dataset_registry.tick_refs() == frozenset({"jp225_tick"})


# --- 計算量（Test Spy・発行−使用=0）: 台帳登録は I/O を 1 件も発行しない ----- #
#
# なぜ必要か（CLAUDE.md 絶対命令・ISSUE-450 同型）:
#   出力（導出される 4 台帳の中身）が正しくても、「ref を 1 つ登録しただけで、その ref の
#   実ファイルを触りに行く」実装は状態検証では**原理的に落ちない**。jp225_mt5 の実 CSV は
#   VM 実測（V-1〜V-5）が通るまで存在しない。台帳の導出が実ファイルの実在に依存すると、
#   登録した瞬間に「まだ無いファイル」への I/O が発行され、無関係な ref の配信まで巻き込む。
#   よって「台帳の導出が発行するファイル I/O = 0」を回数で表明する。
#   固定するのは**無駄の不在**であって呼び出し回数という実装詳細ではない。
def _count_fs_calls(monkeypatch, work) -> int:
    """work() 実行中に発行されたファイルシステム接触の回数を数える（Test Spy）。"""
    calls: list[str] = []
    for owner, name in (
        (pathlib.Path, "exists"),
        (pathlib.Path, "open"),
        (pathlib.Path, "is_file"),
        (pathlib.Path, "stat"),
        (builtins, "open"),
    ):
        original = getattr(owner, name)

        def spy(*a, _o=original, _n=name, **kw):
            calls.append(_n)
            return _o(*a, **kw)

        monkeypatch.setattr(owner, name, spy)
    work()
    return len(calls)


def test_台帳導出はファイルIOを1件も発行しない(monkeypatch):
    """発行した I/O − 出力に使った I/O = 0（出力は純粋にメモリ上の記述子から作られる）。"""

    def derive():
        dataset_registry.whitelist()
        dataset_registry.clamp_refs()
        dataset_registry.rollup_refs()
        dataset_registry.tick_refs()

    assert _count_fs_calls(monkeypatch, derive) == 0


def test_台帳導出のIO発行は登録ref数を増やしても増えない(monkeypatch):
    """オーダーの表明（2 点固定）: 発行 I/O は登録件数に依存しない（実ファイル探索をしない）。

    jp225_mt5 のように**実体がまだ無い** ref を登録しても、導出経路が重くならない／
    失敗しないことを構造的に保証する。
    """
    from marketdata.dataset_registry import DatasetDescriptor

    def derive():
        dataset_registry.whitelist()
        dataset_registry.clamp_refs()
        dataset_registry.rollup_refs()
        dataset_registry.tick_refs()

    base = _count_fs_calls(monkeypatch, derive)

    # 点 2: 実体の無い ref を 20 件足しても発行は増えない。
    probes = {
        f"_probe_{i}": DatasetDescriptor(
            path=DATA_DIR / f"_absent_{i}.csv", symbol="JP225", rollup=True
        )
        for i in range(20)
    }
    monkeypatch.setattr(
        dataset_registry, "REGISTRY", {**dataset_registry.REGISTRY, **probes}
    )
    grown = _count_fs_calls(monkeypatch, derive)

    assert (base, grown) == (0, 0), (
        f"台帳導出が実ファイルを探索している（登録数 4→24 で {base}→{grown} 件発行）"
    )
