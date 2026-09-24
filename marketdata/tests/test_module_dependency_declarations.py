"""docstring が宣言した依存範囲を **検定で強制する**（ISSUE-262）。

なぜ必要か:
    本 repo の是正は繰り返し「コメントに正しいことを書く」で終わっていた。宣言は施行されている
    ように読めるが、施行する仕組みが無ければ次の編集で静かに破れる。実際 ``resample`` は
    「pandas のみに依存」と宣言しながら ``csv_schema`` を、``tick_m1`` は「pandas + paths のみ」と
    宣言しながら ``outlier_policy`` / ``csv_schema`` / ``tail_reader`` を import していた。

本テストの規約:
    各モジュールの **許可 import 集合を明示列挙**し、AST 走査で実 import と突き合わせる。
    関数内の遅延 import も対象にする（宣言を迂回する抜け穴にしないため）。
    依存を増やすときは、docstring と本表の両方を同時に更新することを強制する。
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

_PKG = Path(__file__).resolve().parents[1]

#: モジュール → 許可する外部 import（stdlib と ``__future__`` は常に許可）。
#:
#: 値は **docstring の宣言と一致していなければならない**。宣言を広げるなら、その理由を
#: 当該モジュールの docstring へ書いたうえで本表も広げる（片方だけの更新を許さない）。
_ALLOWED: "dict[str, set[str]]" = {
    # 時間足台帳（唯一源）。**依存ゼロ**＝stdlib（typing と、期間先頭日の暦算術に使う datetime）
    # のみ。pandas を持ち込むと「pandas を使えない純層も同じ台帳から導出する」という分離目的
    # （ISSUE-261）が崩れる。
    "tf_ledger.py": set(),
    # Dukascopy ネイティブ生列の唯一源（ISSUE-502 C-1）。**依存ゼロ**の定数モジュール
    # （csv_schema と対称）。産出者 dukascopy_source と下流 ingest がここから引く。
    "tick_raw_schema.py": set(),
    # セッション日境界の唯一源。週/月ラベル規則は resample、暦ラベル tf 集合と期間先頭日の
    # 暦算術は tf_ledger（いずれも唯一源）へ委譲する。この 2 エントリを消すと、同じ暦算術の
    # 手書き複製が本モジュールへ復活する（ISSUE-479 M-3）。
    "session_day.py": {"numpy", "pandas", "marketdata.resample", "marketdata.tf_ledger"},
    # 純規則層。csv_schema / tf_ledger はいずれも依存ゼロの定数モジュール
    # （前者は集約対象列の唯一源・後者は時間足台帳の唯一源）。
    "resample.py": {"pandas", "marketdata.csv_schema", "marketdata.tf_ledger"},
    # comma 形式 CSV → Candle の adapter。``datawindow.half_open`` は取得窓 `[start, end)` の
    # 境界正規化と半開判定の唯一の実体（ISSUE-401 🟡-2）。本 adapter が自前で
    # ``int(start.timestamp())`` を持つと naive datetime をローカル TZ で解釈し、同じ窓を受ける
    # Bar 段（UTC 解釈）と食い違う（実測 32400 秒差）。**この 1 エントリを消すと複製が復活する**
    # ため、依存として明示し検定で固定する。
    "csv_source.py": {"pandas", "datawindow.half_open", "marketdata.port"},
    # datasetRef 記述子レジストリ（唯一源）。物理基点（paths）**のみ**に依存する最下層 peer。
    # docstring は当初からこう宣言していたが、機械的に強制されていなかった（ISSUE-512 段階 1 で
    # 実測・TBD-5 承認）。ここへ 1 つでも import を足すと tf_meta↔dataset の相互依存や
    # 「台帳が読取側を知る」逆流が入り込む。宣言を検定へ昇格させる。
    "dataset_registry.py": {"marketdata.paths"},
    # 供給の鮮度判定（ISSUE-526 段 1）。素材の所有者側に置き、暦の権威（simulator / indigators）
    # へは依存しない。末尾読み・銘柄の台帳・M1 の置き場の権威・物理基点の 4 つだけを参照する。
    "supply_freshness.py": {
        "pandas",
        "marketdata.paths",
        "marketdata.tail_reader",
        "marketdata.dataset_registry",
        "marketdata.tick_m1",
    },
    # tick 木レイアウトの唯一権威。物理基点（paths）と日付解決（pandas）だけに依存し、
    # 素材化モジュール（tick_m1）へは依存しない＝権威が利用者へ逆流しない（ISSUE-479 M-2）。
    "tick_tree.py": {"pandas", "marketdata.paths"},
    # ロールアップ配置レイアウトの唯一権威。物理基点（paths）だけに依存し、生成側（rollup）・
    # 読取側（rollup_store）へは依存しない＝権威が利用者へ逆流しない（ISSUE-502 D-16）。
    # pandas すら要らない（純粋なパス組み立て）。置き場の名前（series）は台帳が唯一源
    # （ISSUE-511 段階 1d）。台帳は paths のみに依存する最下層ゆえ循環しない。
    "rollup_paths.py": {"marketdata.paths", "marketdata.dataset_registry"},
    # M1 素材化。外れ値方針・CSV スキーマ・末尾読取は marketdata 内の下位部品。
    # ``marketdata.keep_last`` は「同一キーの最終出現を採る」規則の唯一の実体（依存ゼロの中立核・
    # ISSUE-479 F-6）。この 1 エントリを消すと _dedupe_minutes に同じ式の複製が復活する。
    "tick_m1.py": {
        "pandas",
        "marketdata.paths",
        "marketdata.outlier_policy",
        "marketdata.csv_schema",
        "marketdata.tail_reader",
        "marketdata.keep_last",
        # M1 の置き場の名前（series）の唯一源（ISSUE-511 段階 1d）。台帳は paths のみに依存する
        # 最下層ゆえ循環しない。この 1 エントリを消すと m1_csv_path が ref 名から組む写しへ戻る。
        "marketdata.dataset_registry",
        # tick 木レイアウトの唯一権威（ISSUE-479 M-2）。この 1 エントリを消すと、木の形を
        # 組む式が本モジュールへ復活し、レイアウト権威が 2 箇所になる。
        "marketdata.tick_tree",
        # spread 列の規則の唯一源（ISSUE-511 段階 2）。pandas のみに依存する下位部品ゆえ循環しない。
        # この 1 エントリを消すと、気配幅の丸め規則が本モジュールへ手書きで復活する。
        "marketdata.quote_spread",
        # spread の point の読み口（ISSUE-511 段階 3 前提 (a)）。台帳と銘柄仕様スナップショットにのみ
        # 依存する下位部品ゆえ循環しない。この 1 エントリを消すと、point が呼出ごとの引数へ戻る。
        "marketdata.spread_point",
    },
    # spread の point の読み口（ISSUE-511 段階 3 前提 (a)）。台帳（どのスナップショットか）と
    # スナップショット（point の値）の 2 つだけ。pandas も tick_m1 も知らない。
    "spread_point.py": {"marketdata.dataset_registry", "marketdata.symbol_spec_snapshot"},
    # MT5 端末スナップショットの読み口。docstring は依存ゼロを宣言していたが検定表に行が無かった
    # （ISSUE-511 段階 3 前提 (a) で昇格）。
    "symbol_spec_snapshot.py": set(),
    # 分内の気配幅（spread）規則の唯一源（ISSUE-511 段階 2）。pandas のみ。point は呼出側が
    # 注入する（銘柄仕様 symbol_spec_snapshot を import すると規則が供給元に縛られる）。
    "quote_spread.py": {"pandas"},
    # loader 互換 CSV スキーマの唯一源。**依存ゼロ**の定数モジュール（tick_m1 / rollup /
    # resample が参照する側。ここへ import を足すと循環の入口になる）。
    "csv_schema.py": set(),
    # 日別ティックの読み元の解決と読取（ISSUE-512 段階 2）。確定 parquet（tick_tree のレイアウト・
    # tick_m1 の列と集計規則）と受信ジャーナル（mt5_ticks の形式とサーバ時刻 → UTC 変換）の
    # **上位** に置く。mt5_ticks は tick_m1 を import するため、tick_m1 側へ置くと循環になる。
    "tick_day_source.py": {
        "pandas",
        "marketdata.paths",
        "marketdata.tick_tree",
        "marketdata.tick_m1",
        "marketdata.mt5_ticks",
    },
}

_STDLIB_PREFIXES = {
    "__future__", "typing", "pathlib", "datetime", "os", "sys", "re", "json", "csv",
    "time", "math", "logging", "tempfile", "collections", "dataclasses", "functools",
    "itertools", "hashlib", "zlib", "queue", "threading", "urllib", "shutil", "glob",
    "zoneinfo", "types",
}


def _external_imports(path: Path) -> "set[str]":
    """モジュール内の全 import（関数内の遅延 import を含む）から外部依存名を集める。"""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    out: "set[str]" = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                out.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.level:            # 相対 import は自パッケージ内＝対象外
                continue
            module = node.module or ""
            if module == "marketdata":
                # `from marketdata import X` は marketdata.X として数える（粒度を揃える）。
                for alias in node.names:
                    out.add(f"marketdata.{alias.name}")
            else:
                out.add(module)
    return {n for n in out if n.split(".")[0] not in _STDLIB_PREFIXES}


@pytest.mark.parametrize("filename", sorted(_ALLOWED))
def test_module_imports_match_the_declared_dependency_set(filename):
    """実 import が宣言（本表）を超えていない。

    超えていたら、docstring の依存宣言が事実と食い違っている。依存を足すのが正しいなら
    docstring と本表を同時に更新する。足すべきでないなら import を消す。
    """
    got = _external_imports(_PKG / filename)
    extra = got - _ALLOWED[filename]
    assert not extra, (
        f"{filename} が宣言外の依存を持っています: {sorted(extra)}。"
        " docstring の依存宣言と本表を同時に更新するか、import を撤去してください。"
    )


@pytest.mark.parametrize("filename", sorted(_ALLOWED))
def test_declared_dependency_set_has_no_stale_entries(filename):
    """本表に、実際には使われていない許可エントリが残っていない（宣言の陳腐化を防ぐ）。"""
    got = _external_imports(_PKG / filename)
    stale = _ALLOWED[filename] - got
    assert not stale, (
        f"{filename} の許可表に未使用のエントリが残っています: {sorted(stale)}。"
        " 依存が消えたら宣言側も狭めてください。"
    )
