"""A-EaBuildProbe: 使い捨ての最小データで EA を 1 度だけ組み立てる（adapter・ISSUE-405）。

**責務は 1 つ**: 「その ea_name の構築物を得る」ための使い捨てデータセットを用意し、
注入された構築関数を呼ぶこと。得られた構築物の**解釈はしない**（系列名を読むのも、
SL 設定名を探すのも、それぞれのカタログの仕事である）。

なぜ独立させたか（SRP・複製禁止）: 系列カタログ（E-3・§12.5）と SL 設定カタログ
（§12.8）は**別の問い**を持つが、「EA を組み立てるための最小データを用意する」段は同一
である。各カタログに書くと、探索用サンプルの形式・行数・フォールバック順という同じ
知識が 2 箇所に写る（片方だけ改訂されて必ず食い違う）。

探索方法: 各 EA ファクトリは自分でデータファイルを読む（comma 形式 / MT5 タブ区切り）。
どちらを読むかは `simulator.main` 側の知識なので、**両形式の最小サンプルを書いて順に試し、
成功した方を採る**。数行の DataFrame で足りる（必要なのは構築が成立することだけで、
値は使わない）。

DIP: 構築関数は**注入**で受ける（既定束縛を持たない）。`simulator.main` を直接掴むと依存が
外向き（adapter → main）になり、束縛の差し替え点が消える。束ねるのは Composition Root
（`sim_ui/main/composition_root_jobs.py`）である。前例は `report_payload_writer`（R-4）。
"""
from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any, Callable

# 探索用の最小サンプル。指標の warmup を満たす程度の行数があればよい（値は使わない）。
_ROWS = 12

#: 指標の周期。12 行のサンプルで warmup を満たせる最小値にする。他の構築引数
#: （`weekly_*` 等）は渡さない——既定値は `simulator.main` 側が単一ソースとして持つ。
_PROBE_MA_PERIOD = 2
_PROBE_MA_METHOD = "sma"
_PROBE_ADX_PERIOD = 2


def _comma_csv() -> str:
    """comma 形式（`CsvOHLCRepository` 系ローダ）が読む最小サンプル。"""
    head = "time,open,high,low,close,volume\n"
    rows = "".join(
        f"2024-01-{i + 1:02d}T00:00:00,{100 + i},{101 + i},{99 + i},{100 + i},1\n"
        for i in range(_ROWS)
    )
    return head + rows


def _mt5_tsv() -> str:
    """MT5 形式（タブ区切り・`<DATE>` 見出し）ローダが読む最小サンプル。"""
    head = "<DATE>\t<TIME>\t<OPEN>\t<HIGH>\t<LOW>\t<CLOSE>\t<TICKVOL>\t<VOL>\t<SPREAD>\n"
    rows = "".join(
        f"2024.01.{i + 1:02d}\t00:00:00\t{100 + i}\t{101 + i}\t{99 + i}\t{100 + i}\t1\t0\t2\n"
        for i in range(_ROWS)
    )
    return head + rows


class EaBuildProbe:
    """注入された構築関数を、使い捨てデータセットで ea_name ごとに 1 度呼ぶ。"""

    def __init__(self, build: "Callable[..., Any]") -> None:
        """``build``: ジョブ仕様（`**spec`）を受けて構築物を返す関数（**必須**）。

        束縛の実体は `simulator.main.build_ea_indicators` / `build_ea_strategy` であり、
        その選択は Composition Root だけが知る。既定値を置かないのは R-4 と同型の
        Fail-Stop である（既定束縛があると依存の向きが外向きに戻る）。
        """
        self._build = build

    def for_ea(self, ea_name: str) -> Any:
        """``ea_name`` の構築物を返す。どの形式でも組めなければ例外を送出する。

        送出する例外は最後に失敗した形式のもの（原因が呼出側の診断に届くように、
        新しい例外型で包み直さない）。
        """
        with tempfile.TemporaryDirectory(prefix="sim_ui_ea_probe_") as tmp:
            root = Path(tmp)
            candidates = (
                _write(root / "probe.mt5.csv", _mt5_tsv()),
                _write(root / "probe.csv", _comma_csv()),
            )
            last_error: "Exception | None" = None
            for data_path in candidates:
                try:
                    return self._build(
                        data_path=data_path,
                        ea_name=ea_name,
                        ma_period=_PROBE_MA_PERIOD,
                        ma_method=_PROBE_MA_METHOD,
                        adx_period=_PROBE_ADX_PERIOD,
                    )
                except Exception as exc:  # この形式では読めない → 次の形式を試す
                    last_error = exc
        raise RuntimeError(
            f"{ea_name} を探索用データセットで構築できませんでした: {last_error!r}"
        )


def _write(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    return path
