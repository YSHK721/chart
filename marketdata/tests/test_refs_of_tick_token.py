"""ティック木の枝名から、その木を読む datasetRef を**すべて**引く口（ISSUE-511 段階 8-D-2b 段 1・D-3）。

用語（初出定義）:
    ティック木の枝名（token）
        ＝ 保存木 <DATA_DIR>/ticks/YYYY/MM/DD/<token>_ticks.parquet の <token>。木の形の権威は
          marketdata/tick_tree.py、どの枝かの権威は台帳（`REGISTRY` の記述子の tick_token 欄）。
    系列集合
        ＝ 同じ木から畳んで作られる datasetRef の集まり。いまは 1 つの木（MT5 の 1 サーバ 1 口座）を
          spread 列の有無ちがいの 2 つの ref が共有している。

なぜ台帳が系列集合を持つのか（設計裁定 2026-09-23・ISSUE-511 段階 8-D-2b D-3）:
    いま系列集合を決めているのは常駐の CLI 引数（運用者が台帳の事実を再宣言する形）で、その形が
    実際に 9 日間の凍結を生んだ（spread 付き系列を誰も publish せず 2026-09-14 で止まった）。
    所有者を台帳へ一本化する。先例は同じ台帳が持つ「木 → 価格基準」の口
    （`dataset_registry.price_basis_of_tick_token`）。

本検定が固定するもの:
  R-1 集合: 木のトークン 1 件の照会で、その木を読む ref が**全部**返る。期待値は綴りを書き写さず
      別属性（vendor）から導く（書き写すと台帳が変わっても緑のまま残る）。
  R-2 全射: 台帳のどのティック ref も、自分の木を引いた答えに自分が入っている（走査は台帳全件）。
  R-3 順序: 答えの並びは台帳の宣言順に追随する（並べ替えた台帳では答えも並べ替わる）。集合や
      ハッシュ順・名前順に頼っていればここで落ちる。
  R-4 決定性: 同じ入力を二度引いて同じ並びが返る。
  R-5 Fail-Stop: 台帳にその木が無ければ案内つきで止まる。空 tuple を返さない。
      None（＝ティック木を持たない記述子の tick_token 欄の値）で照会しても、木を持たない ref の
      群れが返ってはならない。
  CX-1 計算量: 台帳からの導出はファイル IO を 1 件も発行しない（発行 − 使用 = 0）。
  CX-2 計算量: 同じ木を読む ref を増やしても発行は増えない（規模 2 点・回数は焼き込まない）。

計算量の規約（絶対命令 2026-08-28）: 回数そのものは期待値に焼き込まない。固定するのは
「発行 − 使用 = 0」と「入力の規模を変えても発行が増えないこと」だけである。

本段（段 1）は**台帳に口を足すだけ**で、呼び手（常駐・usecase・adapter）は 1 バイトも変えない。
ファイル書込は 1 件も無い（照会であって検証ではない）。構造: Arrange-Act-Assert（AAA）。
"""
from __future__ import annotations

import pytest

from marketdata import dataset_registry
from marketdata.dataset_registry import REGISTRY, DatasetDescriptor
from marketdata.paths import DATA_DIR

# ファイル IO の Test Spy は既存検定が持つものをそのまま使う（同じ継ぎ目・同じ流儀。写しを作ると
# 片方だけ育って食い違う）。唯一源: marketdata/tests/test_dataset_registry_mt5.py。
from test_dataset_registry_mt5 import _count_fs_calls

#: 台帳に無い木の綴り（Fail-Stop の入力）。実在の枝名と衝突しない語を使う。
_UNKNOWN_TOKEN = "zz_no_such_tick_tree"


def _mt5_tick_refs() -> "tuple[str, ...]":
    """MT5 ベンダのティック ref を台帳の宣言順で導く（**綴りを書き写さない**期待値の素）。

    tick_token とは独立な属性（vendor）から導くので、片方の記述子の tick_token だけを別の綴りへ
    変える変異は、この期待値と答えの食い違いとして現れる。台帳の宣言では MT5 のティック ref は
    すべて同じ端末（1 サーバ 1 口座）の受信ジャーナルから作られる＝同じ木を読む。
    """
    return tuple(ref for ref, d in REGISTRY.items() if d.tick and d.vendor == "mt5")


# =====================================================================
# R-1. 集合は台帳から導かれる
# =====================================================================
def test_mt5の木の照会は同じ木を読むrefを全部返す():
    """R-1: MT5 の木のトークン 1 件で、その木を読む ref が全部返る（先端比較の入力になる集合）。

    走査範囲は `REGISTRY` 全件。時点 2026-09-23 で tick かつ vendor が mt5 の ref は 2 件
    （spread 列なしと spread 列つき）であり、両方が同じ木を読む宣言になっている。
    """
    # Arrange（期待値は別属性から導く。トークンの綴りも台帳から取る）
    expected = _mt5_tick_refs()
    assert len(expected) >= 2, "MT5 のティック ref が 2 件未満（この検定が空振りしている）"
    token = REGISTRY[expected[0]].tick_token

    # Act
    got = dataset_registry.refs_of_tick_token(token)

    # Assert
    assert got == expected


# =====================================================================
# R-2. 全射（台帳のどのティック ref も自分の答えに入る）
# =====================================================================
@pytest.mark.parametrize(
    "ref", [ref for ref, d in REGISTRY.items() if d.tick], ids=lambda r: r
)
def test_台帳のティックrefは自分の木の答えに入っている(ref):
    """R-2: どのティック ref も、自分の tick_token を引いた答えの中に自分が居る（取りこぼし無し）。"""
    # Arrange
    token = REGISTRY[ref].tick_token

    # Act
    got = dataset_registry.refs_of_tick_token(token)

    # Assert
    assert ref in got


# =====================================================================
# R-3 / R-4. 順序は台帳の宣言順・決定的
# =====================================================================
def test_答えの並びは台帳の宣言順に追随する(monkeypatch):
    """R-3: 台帳の宣言順を逆にすれば答えの並びも逆になる（並びの出所は台帳であって名前順ではない）。

    集合・ハッシュ順・名前順で作っていると、この検定で食い違う（いまの 2 件は名前順と宣言順が
    一致しているため、宣言順を動かさなければ両者を区別できない）。
    """
    # Arrange
    forward = dataset_registry.refs_of_tick_token(REGISTRY[_mt5_tick_refs()[0]].tick_token)
    reversed_ledger = dict(reversed(list(REGISTRY.items())))
    monkeypatch.setattr(dataset_registry, "REGISTRY", reversed_ledger)

    # Act
    got = dataset_registry.refs_of_tick_token(REGISTRY[_mt5_tick_refs()[0]].tick_token)

    # Assert
    assert got == tuple(reversed(forward))


def test_同じ木を二度引いても同じ並びが返る():
    """R-4: 決定性（呼び手は複数系列の先端をこの順で突き合わせるため、実行ごとに変わっては困る）。"""
    # Arrange
    token = REGISTRY[_mt5_tick_refs()[0]].tick_token

    # Act
    first = dataset_registry.refs_of_tick_token(token)
    second = dataset_registry.refs_of_tick_token(token)

    # Assert
    assert first == second


# =====================================================================
# R-5. Fail-Stop（0 件は案内つきで止まる）
# =====================================================================
def test_台帳にない木は案内つきで止まる():
    """R-5: 0 件は空 tuple ではなく送出。案内は木の綴り・台帳の所在・記入欄を名指す。

    空 tuple を返すと、呼び手は「publish する系列が 0 件」を正常として受け取り、何も書かないまま
    走り続ける（まさに今回の凍結と同じ形で、出力は形式上正しいため状態検証では検出できない）。
    """
    # Arrange / Act
    with pytest.raises(ValueError) as exc:
        dataset_registry.refs_of_tick_token(_UNKNOWN_TOKEN)

    # Assert（案内が空洞でないこと。要素を 1 つ落とせばここが赤になる）
    message = str(exc.value)
    assert _UNKNOWN_TOKEN in message, "案内が引けなかった木の綴りを名乗っていない"
    assert "dataset_registry" in message, "案内が台帳の所在を名指していない"
    assert "tick_token" in message, "案内が記入すべき欄を名指していない"


def test_木を持たない記述子の値では木を持たないrefが返らない():
    """R-5（境界）: None（tick_token 欄の空値）での照会は Fail-Stop。

    素朴に「tick_token が引数と等しい記述子」を集めると、None での照会で**木を持たない ref**
    （日足・サンプル等）がまとめて返る。返ってしまえば呼び手はティックの無い系列へ publish を
    試み、出力は形式上正しいまま空振りする。
    """
    # Arrange
    tokenless = [ref for ref, d in REGISTRY.items() if d.tick_token is None]
    assert tokenless, "tick_token を持たない ref が台帳に無い（この検定が空振りしている）"

    # Act / Assert
    with pytest.raises(ValueError):
        dataset_registry.refs_of_tick_token(None)


# =====================================================================
# CX-1 / CX-2. 計算量（Test Spy・発行 − 使用 = 0）
# =====================================================================
def test_木からrefを引くのはファイルIOを1件も発行しない(monkeypatch):
    """CX-1: 発行した IO − 出力に使った IO = 0（答えは純粋にメモリ上の記述子から作られる）。

    台帳の照会が実ファイルを触りに行くと、まだ実体の無い系列（新設直後）を登録しただけで
    無関係な経路まで巻き込んで止まる。固定するのは**無駄の不在**であって呼び出し回数ではない。
    """
    # Arrange
    token = REGISTRY[_mt5_tick_refs()[0]].tick_token

    # Act / Assert
    assert _count_fs_calls(monkeypatch, lambda: dataset_registry.refs_of_tick_token(token)) == 0


def test_同じ木を読むrefを増やしてもIO発行は増えない(monkeypatch):
    """CX-2: オーダーの表明（規模 2 点）。答えは 2 件から 22 件へ増えるが、発行 IO は両点とも 0。

    答えが増えていることも併せて見る（増えていなければ「増えても増えない」は空振りである）。
    """
    # Arrange
    token = REGISTRY[_mt5_tick_refs()[0]].tick_token
    base_calls = _count_fs_calls(
        monkeypatch, lambda: dataset_registry.refs_of_tick_token(token)
    )
    base_len = len(dataset_registry.refs_of_tick_token(token))
    probes = {
        f"_probe_{i}": DatasetDescriptor(
            path=DATA_DIR / f"_absent_probe_{i}.csv",
            symbol="JP225",
            tick=True,
            tick_token=token,
            price_basis="bid",
            vendor="mt5",
        )
        for i in range(20)
    }
    monkeypatch.setattr(dataset_registry, "REGISTRY", {**REGISTRY, **probes})

    # Act
    grown_calls = _count_fs_calls(
        monkeypatch, lambda: dataset_registry.refs_of_tick_token(token)
    )
    grown_len = len(dataset_registry.refs_of_tick_token(token))

    # Assert
    assert grown_len == base_len + 20, "規模 2 点目で答えが増えていない（空振り）"
    assert (base_calls, grown_calls) == (0, 0), (
        f"台帳の照会が実ファイルを探索している（答え {base_len}→{grown_len} 件で"
        f" {base_calls}→{grown_calls} 件発行）"
    )
