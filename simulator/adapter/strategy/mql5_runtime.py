"""MQL5 の言語・実行環境の意味論の**単一所有者**（ISSUE-445 段階 3-B 後始末）。

MT5 の EA（``.mq5``）を Python へ移植すると、戦略ロジックとは別に「MQL5 の組込み関数が
Python と違う挙動をする」ぶんの埋め合わせが要る。段階 1・3-B の移植では、その埋め合わせを
各戦略が自前の private メソッドとして持ったため、``MathRound`` / ``NormalizeDouble`` /
銘柄仕様アクセスの 3 つが 3 ファイルに **AST 完全一致で手書き複製**された（実測）。
本モジュールはその 3 つを単独で所有し、戦略側は参照するだけにする。

**責務境界（厳守）**

* 置いてよいもの: MQL5 の組込み関数・実行環境（銘柄仕様の読み取り）の意味論だけ。
  「同じ入力に対し MT5 が返す値」が定義であり、この判断に戦略は一切関与しない。
* 置いてはならないもの: 戦略ロジック（シグナル判定・発注方針・ロット方針・SL/TP 算出）。
  とくに ``NormalizeLot`` は**ここに置かない**。原典 ``.mq5`` 3 本の ``NormalizeLot`` は
  同名だが**同一関数ではない**（同一入力 ``step=0.0 / min=3.0 / lot=10.0`` に対し
  ``2026-03_ma-limit`` は ``10.0``・``2026-04_stop-probe`` は ``9.0`` を返す。実測・
  ``simulator/tests/unit/test_normalize_lot_originals_diverge.py`` が固定）。
  ``NormalizeLot`` は各 EA が自分の都合で書いた**戦略側のコード**であり、共通化すると
  参照実装違反になる。各戦略に残す。

**再発防止**

「戦略ファイルが MQL5 プリミティブを自前で持てる」ことが複製の根本原因であるため、
規約ではなく機械的検査で塞ぐ。``simulator/tests/unit/test_mql5_primitive_single_ownership.py``
が ``simulator/adapter/strategy/*.py`` を AST 走査し、本モジュールが所有する関数の
再実装（構造一致または同名）を検出したら赤にする。本モジュールに関数を追加すれば、
その関数も自動的に検査対象になる（所有一覧は本モジュールから導出しており、
検査側にハードコードしていない）。

出典（すべて実測・原典を Read して確認）:

* ``simulator/tests/fixtures/mt5/ma_slope_jp225_202501/expert/MA_Slope_EA.mq5:157-175``
* ``simulator/tests/confirmation/2026-03_ma-limit/ea.mq5:299-311``
* ``simulator/tests/confirmation/2026-04_stop-probe/ea.mq5:159-171``
"""
from __future__ import annotations

import math
from typing import Any

__all__ = ["math_round", "normalize_double", "spec_value"]


def math_round(x: float) -> float:
    """MQL5 ``MathRound``（絶対値 0.5 を切り上げ＝ゼロから遠ざかる丸め）。

    Python 組込み ``round`` は銀行家丸め（実測: ``round(2.5) == 2`` / ``round(0.5) == 0``）で
    原典と境界の挙動が食い違うため使わない。
    """
    magnitude = math.floor(abs(x))
    if abs(x) - magnitude >= 0.5:
        magnitude += 1.0
    return math.copysign(magnitude, x)


def normalize_double(value: float, digits: int) -> float:
    """MQL5 ``NormalizeDouble(value, digits)``（指定小数桁への丸め・0.5 は切り上げ）。"""
    scale = 10.0**digits
    return math_round(value * scale) / scale


def spec_value(cfg: Any, key: str) -> float:
    """銘柄仕様の 1 項目を読む。MQL5 ``SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_*)`` 相当。

    MQL5 の EA は銘柄仕様を実行時に端末から読む（原典 ``MA_Slope_EA.mq5:159-161`` /
    ``2026-03_ma-limit/ea.mq5:301-303`` / ``2026-04_stop-probe/ea.mq5:65-67``）。Python 側では
    その供給経路が config（``RunConfig`` の strategy_params）であり、本関数がその読み口を担う。

    **キー未供給は ``0.0`` を返す（＝「制約なし」）。この挙動は変更しない。**
    戻り値 ``0.0`` は原典 ``NormalizeLot`` の非正値分岐（``step > 0.0`` / ``max > 0.0`` の
    ガード）にそのまま載り、「その制約は課されない」を意味する。既存テスト・突合スクリプトの
    多くは volume 系キーを供給しておらず、この後方互換に依存している。

    **``RunConfig`` の方針との非対称（記録・是正はしていない）**:
    ``simulator/main/run_config.py:29-33`` は「欠落キーは loud に失敗する＝サイレント補完
    しない」を明示し、``__getitem__`` は欠落で ``KeyError`` を送出する。本関数はその
    ``KeyError`` を握り潰して ``0.0`` に翻訳するため、方針が逆向きである。
    fail-loud 化の可否は ISSUE-445 の後続判断に委ねる（本モジュールでは変更しない）。
    握り潰すのは ``KeyError`` のみであり、型不正等はそのまま送出される。
    """
    try:
        return float(cfg[key])
    except KeyError:
        return 0.0
