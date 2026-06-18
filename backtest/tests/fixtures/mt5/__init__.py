"""MT5 突合テストケースの統一ローダ。

ケース単位の自己完結 fixture (``fixtures/mt5/<case>/``) を読み込み、テストから
cwd 非依存で参照できる ``MT5Case`` を返す。各ケースのディレクトリ構成は README.md 参照。

使い方::

    from backtest.tests.fixtures.mt5 import load_case, list_cases

    case = load_case("ma_slope_jp225_202501")
    deals = case.deals                     # report.json の deals (list[dict])
    pf = case.expected["results"]["profit_factor"]
    csv_path = case.input_csv              # 価格データ Path
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

import yaml

# このパッケージ (= fixtures/mt5/) の絶対パス。cwd に依存しない。
_MT5_ROOT = Path(__file__).resolve().parent

# ウォームアップ込み CSV の命名規則: ファイル名末尾が `_<12桁>_<12桁>.csv`
# （開始/終了の完全タイムスタンプ範囲・例 JP225_M1_202412230100_202501302359.csv）。
# 取引期間 CSV（例 JP225_M1_202501.csv）はこの形に一致しない。
_WARMUP_CSV_RE = re.compile(r"_\d{12}_\d{12}\.csv$")


@dataclass(frozen=True)
class MT5Case:
    """1 件の MT5 突合ケースを表す。

    属性:
        dir:        ケースのルートディレクトリ (Path)。
        config:     case.yaml をパースした dict。
        input_csv:  取引期間の価格データ CSV の Path (input/ 配下・warmup でない正準データ)。
        warmup_csv: ウォームアップ込み CSV の Path (`_<12桁>_<12桁>.csv` 命名)。
                    存在しなければ None（後方互換: 従来ケースは warmup CSV を持たない）。
        expert_mq5: EA 原典の Path (expert/*.mq5)。
        expected:   expected/report.json をパースした dict
                    (top: source / settings / results / deals_count / deals)。
        deals:      expected["deals"] (list[dict]) のショートカット。
    """

    dir: Path
    config: dict
    input_csv: Path
    expert_mq5: Path
    expected: dict
    deals: list
    warmup_csv: Path | None = None


def list_cases() -> list[str]:
    """利用可能なケースのディレクトリ名一覧を返す (昇順)。"""
    return sorted(
        p.name
        for p in _MT5_ROOT.iterdir()
        if p.is_dir() and (p / "case.yaml").is_file()
    )


def _single_file(directory: Path, pattern: str) -> Path:
    """directory 直下で pattern に一致する唯一のファイルを返す。"""
    matches = sorted(directory.glob(pattern))
    if len(matches) != 1:
        raise FileNotFoundError(
            f"{directory}/{pattern} は 1 件である必要があるが {len(matches)} 件: {matches}"
        )
    return matches[0]


def _select_csvs(input_dir: Path) -> "tuple[Path, Path | None]":
    """input_dir 配下の CSV を「取引期間 CSV」と「warmup CSV」へ弁別する。

    warmup CSV は `_<12桁>_<12桁>.csv` 命名（フル期間レンジ）で識別する。取引期間 CSV は
    それ以外で唯一でなければならない（warmup は 0/1 件）。warmup 併存ケース・従来の
    単一 CSV ケースの双方を後方互換に扱う。
    """
    csvs = sorted(input_dir.glob("*.csv"))
    warmups = [p for p in csvs if _WARMUP_CSV_RE.search(p.name)]
    trading = [p for p in csvs if not _WARMUP_CSV_RE.search(p.name)]
    if len(trading) != 1:
        raise FileNotFoundError(
            f"{input_dir}: 取引期間 CSV は 1 件である必要があるが {len(trading)} 件"
            f"（warmup={len(warmups)} 件）: trading={trading} warmup={warmups}"
        )
    if len(warmups) > 1:
        raise FileNotFoundError(
            f"{input_dir}: warmup CSV は 0 または 1 件である必要があるが "
            f"{len(warmups)} 件: {warmups}"
        )
    return trading[0], (warmups[0] if warmups else None)


def load_case(name: str) -> MT5Case:
    """ケース名 (ディレクトリ名) から ``MT5Case`` を構築する。

    Raises:
        FileNotFoundError: ケースまたは必須ファイルが存在しない場合。
    """
    case_dir = _MT5_ROOT / name
    if not (case_dir / "case.yaml").is_file():
        available = ", ".join(list_cases()) or "(none)"
        raise FileNotFoundError(
            f"MT5 ケース '{name}' が見つからない (case.yaml 不在)。利用可能: {available}"
        )

    config = yaml.safe_load((case_dir / "case.yaml").read_text(encoding="utf-8"))
    expected = json.loads(
        (case_dir / "expected" / "report.json").read_text(encoding="utf-8")
    )

    input_csv, warmup_csv = _select_csvs(case_dir / "input")
    return MT5Case(
        dir=case_dir,
        config=config,
        input_csv=input_csv,
        expert_mq5=_single_file(case_dir / "expert", "*.mq5"),
        expected=expected,
        deals=expected["deals"],
        warmup_csv=warmup_csv,
    )


__all__ = ["MT5Case", "load_case", "list_cases"]
