#!/usr/bin/env python3
"""ティック parquet → M1 原子 CSV（上位足ロールアップ用・A案プロト）。**実行不可**。

Dukascopy ティック（生 parquet）を mid 基準・UTC で 1 分足へ集計し、jp225_tick_m1.csv を出力する。
以降の上位足は proto_server が marketdata.resample.resample_ohlc で生成する（既存基盤を流用）。
これでチャートの足も足内更新も「同じティック（mid・UTC）」由来＝書き変わり無し・整合。

既存 jp225_m1.csv は触らない（新 ref `jp225_tick` の専用ファイルを新規出力）。

fail-stop（ISSUE-479 Wave2 フェーズ 1-E）:
    本ファイルは本番 M1 CSV を**絶対パスで無条件に上書き**する。試作はレビューも
    回帰ゲートも通らない使い捨ての実行なので、その実行が本番データの破壊になり得る
    状態を残せない。後継 `tools/build_tick_rollup.py` が実在し、こちらが正規の経路で
    ある（差分追記・原子的書き出し・列数検査を備える）。

    そこで本モジュールは読み込まれた時点で SystemExit を送出する。以降の定義・
    書き込みはすべて到達不能であり、`tools/tests/test_prototype_write_isolation.py`
    がその不到達性を構文木で確認する。ファイル自体は試作の記録として残す
    （削除は承認事項）。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

#: 後継（正規の経路）。案内と検定の単一の出所。
SUCCESSOR = "tools/build_tick_rollup.py"

raise SystemExit(
    f"prep_tick_rollup.py は実行できません（本番 M1 CSV を無条件に上書きするため）。"
    f"後継 {SUCCESSOR} を使ってください。"
)

TICK_ROOT = Path("/workspaces/app/data/marketdata/ticks")
OUT = Path("/workspaces/app/data/marketdata/jp225_tick_m1.csv")
START = sys.argv[1] if len(sys.argv) > 1 else "2025-01-01"
END = sys.argv[2] if len(sys.argv) > 2 else "2026-06-27"


def day_files(start: str, end: str) -> list[Path]:
    s, e = pd.Timestamp(start), pd.Timestamp(end)
    out, d = [], s
    while d <= e:
        p = TICK_ROOT / f"{d.year:04d}" / f"{d.month:02d}" / f"{d.day:02d}" / "JP225_ticks.parquet"
        if p.is_file():
            out.append(p)
        d += pd.Timedelta(days=1)
    return out


def main() -> None:
    files = day_files(START, END)
    print(f"範囲 {START}..{END}  ティック日数: {len(files)}")
    if not files:
        print("ティック parquet が見つかりません")
        return
    frames = [pd.read_parquet(p, columns=["timestamp", "bidPrice", "askPrice"]) for p in files]
    df = pd.concat(frames, ignore_index=True)
    df["mid"] = (df["bidPrice"] + df["askPrice"]) / 2.0
    df["date"] = df["timestamp"].dt.tz_localize(None).dt.floor("min")   # UTC naive・分床
    g = df.groupby("date")["mid"]
    m1 = pd.DataFrame({
        "open": g.first(), "high": g.max(), "low": g.min(), "close": g.last(),
        "volume": g.size().astype(float),       # その分のティック数
    })
    OUT.parent.mkdir(parents=True, exist_ok=True)
    m1.to_csv(OUT)
    print(f"M1バー: {len(m1):,}  ({m1.index[0]} .. {m1.index[-1]})  -> {OUT}")


if __name__ == "__main__":
    main()
