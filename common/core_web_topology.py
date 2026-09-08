"""配信トポロジの唯一源（アクター非依存の中立核・ISSUE-504 抜本要素 (ii)）。

## 何の台帳か

「どの core がどの根から配信し、そこで見つからないとき **どの根へ落ちるか**」の一覧である。
配信規則そのものの参照実装は simulator/replay_ui/framework/static_file_server.py の resolve
（自根で解決 → miss なら共有根へ同一 rel でフォールバック）であり、本モジュールが持つのは
その規則の**適用対象**、すなわち根の名前だけである。規則と対象を分けるのは、規則が言語ごとに
1 本ずつ（Python の配信器・JS の検定）必要なのに対し、対象は 1 つしか無いからである。

## なぜ台帳が要るか（重複列挙の除去）

同じ事実が 4 つの Composition Root へ手書きで写されていた:

  - dashboard_ui/main/composition_root.py
  - simulator/replay_ui/main/composition_root.py
  - simulator/sim_ui/main/composition_root.py
  - simulator/sim_ui/main/composition_root_jobs.py

共有根を動かすには、閉じているはずの束縛点 4 枚を同時に開いて直す必要があり（OCP 違反）、
3 枚だけ直すと残る 1 core が URL 同一のまま 404 になる。症状は「特定の画面でだけモジュールが
読めない」という遠い形で出る。加えて、配信検定（JS 側）が走査根を自前で列挙していたため
dashboard / sim / unified は無音で無検査だった——列挙は新規を永久に検出しない。

## なぜ中立核（common）が所有するか

消費者は別アクターに属する 3 者以上である（dashboard core / simulator の 2 core / 配信検定）。
実体をどれかへ置くと subsystem 間の横向きの辺が生まれる。common.shared_web_roots
（供給パッケージの台帳）・common.dev_paths と同一の解である。

**shared_web_roots とは別の台帳のままにする**。あちらは「どのパッケージがフロントの実体を
供給するか」（アクター: 共有供給パッケージの管理者）で、こちらは「どの core がどこから配信し
どこへ落ちるか」（アクター: 配信トポロジの運用者）である。変わる理由が違うので束ねない。

## core と統合層

cores の集合はモード定義表（unified_ui/web/js/mode_table.js）と一致しなければならない
（突合は tools/tests/test_core_web_topology_ledger.py）。統合層 unified_ui はモードではない
ので cores には入れないが、配信される JS は同じ規則で解決されるため走査根には含める。

## 依存方向

本モジュールは **stdlib のみ**に依存する（common の中立核規約）。読み取りは 1 プロセス 1 回
（消費者は core ごとに問い合わせるので、都度読むと読取が core 数に比例して増える。増えた
読取は答えを 1 文字も変えないため状態検証では原理的に落ちない）。
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

#: 台帳の実体。本ファイルの位置から一意に決まる（cwd・引数に依存させない）。
LEDGER_PATH = Path(__file__).resolve().with_suffix(".json")

_CORES = "cores"
_INTEGRATION = "integration"
_WEB = "web"
_FALLBACKS = "fallbacks"


def read_ledger_text() -> str:
    """台帳の本文を読む。

    **実 I/O はここ 1 箇所だけ**にする。計算量テストはこの関数を Test Spy へ差し替えて
    発行回数を数えるため、読み取り口が複数あると数え漏れる（＝浪費を通す）。
    """
    return LEDGER_PATH.read_text(encoding="utf-8")


@lru_cache(maxsize=None)
def topology() -> "dict[str, dict[str, dict]]":
    """台帳の中身（core 名 → 配信根とフォールバック根）。

    1 件も取れない台帳は即座に落とす。空を黙って返すと、突合検定が「両辺とも空」で通り、
    検定が在るのに何も守らない状態になる（最悪の失敗形）。
    """
    data = json.loads(read_ledger_text())
    if not data.get(_CORES):
        raise AssertionError(f"配信トポロジ台帳に core が 1 件も無い: {LEDGER_PATH}")
    return data


def _entry(name: str) -> "dict":
    data = topology()
    for section in (_CORES, _INTEGRATION):
        found = data.get(section, {}).get(name)
        if found is not None:
            return found
    raise KeyError(f"配信トポロジ台帳に載っていない配信面です: {name!r}")


def _names(section: str) -> "tuple[str, ...]":
    return tuple(topology().get(section, {}))


#: 配信 core の名前（モード定義表の id と一致する）。
CORE_NAMES: "tuple[str, ...]" = _names(_CORES)

#: 統合層の名前（モードではないが同じ配信規則で解決される）。
INTEGRATION_NAMES: "tuple[str, ...]" = _names(_INTEGRATION)


def web_root(name: str, repo_root: Path) -> Path:
    """その配信面の自根（`<repo>/<core>/web`）。"""
    return Path(repo_root) / _entry(name)[_WEB]


def fallback_roots(name: str, repo_root: Path) -> "tuple[Path, ...]":
    """自根で解決できなかったときに落ちる先（順序が優先順位）。落ちる先が無ければ空。"""
    return tuple(
        Path(repo_root) / rel for rel in _entry(name).get(_FALLBACKS, ())
    )


class NoFallbackRootError(LookupError):
    """その配信面には落ちる先が無い（台帳の fallbacks が空）。

    台帳に載っていない配信面（KeyError）と**別の失敗**である。載ってはいるが単独で配信し、
    落ちる先を持たない面が実在する（live は共有元そのものなので落ちる先が無い）。名前を
    付けておかないと、素の IndexError が起動時に出て「台帳のどの行の話か」が読み取れない。
    """


def primary_fallback_root(name: str, repo_root: Path) -> Path:
    """最優先の落ち先（各 Composition Root が共有根の既定として束ねる 1 本）。

    「優先順位の先頭を取る」を各 Root が自分で書くと、落ち先を持たない配信面を渡した日に
    素の IndexError が 4 箇所それぞれで出る。どの配信面の話かは例外に現れず、起動時の
    スタックトレースからは台帳の行が読み取れない。取り出しと失敗の名付けをここへ寄せる。
    """
    roots = fallback_roots(name, repo_root)
    if not roots:
        raise NoFallbackRootError(
            f"配信面 {name!r} は落ち先を持たない（台帳 {LEDGER_PATH} の fallbacks が空）"
        )
    return roots[0]


def scan_roots(repo_root: Path) -> "dict[str, Path]":
    """検査が走査すべき配信根の全体（core ＋ 統合層）。

    名前の表ではなく台帳から導く。モードを 1 つ足して台帳を忘れれば突合検定が落ち、
    台帳へ足せば走査は自動で広がる（検査側は 1 行も書き換わらない）。
    """
    return {
        name: web_root(name, repo_root)
        for name in (*CORE_NAMES, *INTEGRATION_NAMES)
    }
