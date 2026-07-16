"""null_b_kernel — Null B 帰無サロゲートの純カーネル（ISSUE-094 🔴-2 item2）。

超過占有 z(p) の帰無分布（Null B）生成のうち、live 配信版（:mod:`market_profile_zp`）と
オフライン検定版（``analysis/mp_stats/step5_null_b``）で**二重実装されていた統計規則の中核**を
単一の純カーネルへ一元化する。両者はこのカーネルを import して同一実装を共有する
（market_profile_api は venv の .pth で登録済み＝analysis からも import 可能）。

一元化する規則（数学的に同一な部分のみ）:
    - :func:`build_step_matrix`      — (L, G) 分ステップ行列 S。S[:,0]=ln(grid[:,0]/open)、以降は隣接 log 差。
    - :func:`surrogate_logprice_chunk` — 1 チャンク分のサロゲート log 価格連鎖。ブラケット別に「日を跨いで」
                                        リサンプル（days[:, b_of_minute]）し当日 open から乗法連鎖する。
                                        rng 消費（1 チャンク = 1 回の ``rng.integers``）とチャンク順を固定。
    - :data:`CHUNK`                  — サロゲートのチャンク幅（メモリ有界化・rng 消費順の同一性に必須）。

分離される協調部（各呼び出し側が所有・カーネルには含めない）:
    - 格子/ビニング: zp は 1bp log 一様格子（floor(logp/W_LOG)）、step5 は日相対線形行（exp→_row_index）。
      ISSUE-079 で両者は構造的に異なる（zp=production・step5=検定参照実装）ため、ビニングは各側に残す。
    - z / POC* / 配信整形（bins/round/norm）は各側の表示・検定仕様に属する。

依存: numpy のみ（I/O・状態なし）。
"""

from __future__ import annotations

import numpy as np

# サロゲートのチャンク幅。zp と step5 で同値であること（rng 消費順の一致＝二重実装パリティに必須）。
CHUNK = 2000


def build_step_matrix(grids: "np.ndarray", opens: "np.ndarray") -> "np.ndarray":
    """(L, G) の分ステップ行列 S。``S[:,0]=ln(grid[:,0]/open)``、以降は隣接 log 差。

    ``grids``: (L, G) の分末 close グリッド（ffill 済み）。``opens``: (L,) の当日 open。
    サロゲートは S[d'(b), j] を分 j（ブラケット b(j)）ごとに集めて ``ln(open) + cumsum`` で連鎖する。
    """
    lg = np.log(np.asarray(grids, dtype=float))
    S = np.empty_like(lg)
    S[:, 0] = lg[:, 0] - np.log(np.asarray(opens, dtype=float))
    S[:, 1:] = np.diff(lg, axis=1)
    return S


def surrogate_logprice_chunk(
    S: "np.ndarray", log_open: float, b_of_minute: "np.ndarray", *, rng, m: int
) -> "np.ndarray":
    """1 チャンク（m 本）のサロゲート log 価格連鎖 ``(m, G)`` を返す。

    各サロゲートは、分 j をそのブラケット ``b_of_minute[j]`` の一様ランダムな日 d'(b) から採り、
    ``log_open + cumsum_j S[d'(b(j)), j]`` で連鎖する（ŝ(b) 保存・日固有の水準受容構造のみ破壊）。
    rng 消費は 1 チャンクにつき ``rng.integers(0, L, size=(m, K))`` の 1 回（K=ブラケット数）で、
    zp / step5 の二重実装で完全一致させる（チャンク順も呼び出し側の while ループで固定）。価格化
    （zp=log 格子 floor・step5=exp→線形行）は呼び出し側が本結果に適用する。
    """
    L, G = S.shape
    col = np.arange(G)[None, :]
    days = rng.integers(0, L, size=(m, int(b_of_minute.max()) + 1))
    s_surr = S[days[:, b_of_minute], col]
    return log_open + np.cumsum(s_surr, axis=1)
