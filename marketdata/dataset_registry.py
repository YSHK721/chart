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

import datetime as dt
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
        symbol: この ref の価格がどの銘柄のものか（ISSUE-368 工程 2・案 E-1）。呼び値・表示桁は
            :mod:`marketdata.symbol_spec` が銘柄キーで持つ（**別アクター**＝ブローカー規約の
            所有物ゆえ、ここには同居させない）。従来は供給側が銘柄を一度も名乗らず、front が
            ``CHART_SYMBOL='NI225'`` を自称していた。
        clamp_outliers: 読取時 外れ値クランプ（serving 戦略）の対象か（実市場 ref のみ True）。
        rollup: 1 分足原子＋事前生成ロールアップ CSV の供給経路を使うか（メモリ有界化・D-2）。
        tick: 形成中バー/tf-period を供給するティック由来 ref か（ticks parquet を持つ）。
        data_start: 素材が実在する最古日（全期間再構築の既定起点）。ISSUE-479 M-4 段階 A:
            この値は ``tools/build_tick_rollup`` が全期間起点として持っていたが、「その
            データセットの素材がいつから在るか」は台帳の属性であり、パイプライン側の設定では
            ない。持たない ref は None（全期間起点の概念が無い＝日足同梱データ等）。
        tick_token: この ref のティックがどの木の枝に在るか（ISSUE-512 段階 1）。木の形
            ``<DATA_DIR>/ticks/YYYY/MM/DD/<token>_ticks.parquet`` の権威は
            :mod:`marketdata.tick_tree` が持ち、**どの枝を読むか** を本欄が持つ。``symbol``
            とは別物である: symbol は「その価格がどの銘柄のものか」（呼び値・表示桁の引き当て
            キー）、tick_token は「保存木のディレクトリ/ファイル名の語彙」であり、同じ銘柄を
            別供給元から取れば枝は分かれる（段階 3 の jp225_mt5）。``tick`` が False の ref は
            None（ティック木を持たない）。``tick`` が True で本欄が None なら Fail-Stop
            （:func:`tick_tree_token` が :class:`TickTokenMissing` を送出する。既定値へ落とすと
            新しいティック ref が無言で他銘柄の木を読み、出力は形式上正しいため検出できない）。
            握る側が ``except ValueError`` と書くとこの Fail-Stop は網に掛かる。読取側の境界は
            :class:`TickTokenMissing` を名指しして先に通すこと（型名の詳細は同クラスの説明）。
        price_basis: ティックのどの気配を「価格」とするか（ISSUE-515 対策 1）。語彙と規則の唯一源は
            :mod:`marketdata.tick_m1`（``marketdata.tick_m1.PRICE_BASIS_MID`` = ``"mid"`` /
            ``marketdata.tick_m1.PRICE_BASIS_BID`` = ``"bid"``。本モジュールは paths 以外に依存しない
            ため文字列で持ち、値の妥当性は
            ``marketdata/tests/test_tick_price_basis_ledger.py`` が tick_m1 の語彙と突き合わせる）。
            確定足の書き手と同じ基準でなければならない。``tick`` が True なら必須（構築時に拒否）。
        vendor: ティックをどこから受けているか（ISSUE-515 対策 2）。``"dukascopy"``（Dukascopy の配信）／
            ``"mt5"``（OANDA MT5 端末の受信ジャーナル）。ライブ tick バッファは ref ごとに、この
            ベンダの供給口から作る（ISSUE-508 の裁定「ベンダを素材の属性として明示」）。``tick`` が
            True なら必須（構築時に拒否）。
    """

    path: Path
    symbol: str
    clamp_outliers: bool = False
    rollup: bool = False
    tick: bool = False
    data_start: "dt.date | None" = None
    tick_token: "str | None" = None
    price_basis: "str | None" = None
    vendor: "str | None" = None

    def __post_init__(self) -> None:
        # ISSUE-515 対策 1: ティック ref は価格基準を必ず名乗る。**構築時に**拒否する理由は、
        #   読取時の Fail-Stop にすると形成中バーの読取を包む「素材の失敗を握る」包括的 except の
        #   内側で投げることになり、WARNING と素通しへ化けるからである（構築時には網が無い）。
        if self.tick and self.price_basis is None:
            raise ValueError(
                "tick=True の記述子は price_basis（'mid' / 'bid'）を名乗ってください。"
                " 形成中バー・市場プロファイル・ライブバッファがこの基準で価格を畳みます"
                "（確定足の書き手と同じ基準でなければ、確定のたびに表示が跳ねる）。"
            )
        # ISSUE-515 対策 2: ティック ref はベンダ（ライブで受ける供給口）を必ず名乗る。無いと
        #   ライブ tick バッファを作れない。黙って他のベンダのバッファを当てると表示が混ざる。
        if self.tick and self.vendor is None:
            raise ValueError(
                "tick=True の記述子は vendor（'dukascopy' / 'mt5'）を名乗ってください。"
                " ライブ tick バッファは ref ごとに、このベンダの供給口から作ります。"
            )


# datasetRef 記述子レジストリ（唯一源）。挿入順は従来の DATASET_WHITELIST と一致させる。
REGISTRY: dict[str, DatasetDescriptor] = {
    # サンプル（同梱・日足 OHLCV）。合成 golden のためクランプ対象外。
    #
    # symbol="TSLA" の根拠（**推測ではなく同梱ファイルの実測**・ISSUE-368 工程 2）:
    #   1. 同梱の ``examples/6_callbacks/bar_data/TSLA_30min.csv`` と日付が重なる 50 営業日の
    #      うち 49 日で、30 分足から畳んだ日中 high/low が本 CSV の日足 high/low と**完全一致**
    #      （例 2023-02-02: high=196.76 / low=182.61 が両者一致）。上流 repo の同梱データが
    #      TSLA であることは ``examples/6_callbacks/callbacks.py:37`` の既定銘柄とも整合する。
    #   2. 本 CSV の値は分割調整済み（2010-06-29 始点・close=1.5927）。最終分割以降
    #      （2023-04-14 まで）の 640 値はすべて小数 2 桁以内＝**0.01 格子**。それ以前は分割調整の
    #      残差で最大 4 桁になる。呼び値 0.01 はこの「調整前の素の格子」に一致する。
    "sample": DatasetDescriptor(
        path=_WORKSPACE_ROOT
        / "lightweight-charts-python-main"
        / "examples"
        / "4_line_indicators"
        / "ohlcv.csv",
        symbol="TSLA",
    ),
    # JP225 日足（Dukascopy E_N225Jap・外れ値補正済み）。実市場ゆえクランプ対象。
    "jp225": DatasetDescriptor(
        path=DATA_DIR / "jp225_daily.csv", symbol="JP225", clamp_outliers=True
    ),
    # JP225 1分足原子（全時間足はこれを resample）。実市場・ロールアップ経路。
    "jp225_m1": DatasetDescriptor(
        path=DATA_DIR / "jp225_m1.csv", symbol="JP225", clamp_outliers=True, rollup=True
    ),
    # JP225 1分足（ティック由来・原子）。実市場・ロールアップ経路・ティック由来供給。
    # symbol は既存事実の明文化（``tick_m1._DEFAULT_SYMBOL="JP225"`` / ``_DEFAULT_REF="jp225_tick"``）。
    "jp225_tick": DatasetDescriptor(
        path=DATA_DIR / "jp225_tick_m1.csv",
        symbol="JP225",
        clamp_outliers=True,
        rollup=True,
        tick=True,
        # 既存 tick tree の最古日（実測 2012-06-14）。build_tick_rollup の全期間起点は本値の
        # 導出であり、CLI 既定・help 文言とも従来と 1 バイトも変わらない（ISSUE-479 M-4 段階 A）。
        data_start=dt.date(2012, 6, 14),
        # ティック木の枝名（ISSUE-512 段階 1）。既存事実の明文化であり、ディスク上の木は
        # 1 バイトも変わらない。従来この値は木のレイアウト権威（marketdata/tick_tree.py:30）が
        # 持つ既定引数と、読取側の手書き写像に散っていた（台帳に写像が無かった）。
        tick_token="JP225",
        # 価格基準（ISSUE-515 対策 1）。既存事実の明文化: 確定足の書き手（tools/live_tick_watch.py・
        # tools/build_tick_rollup.py）は price_basis を渡さず既定 mid で畳んでいる（ISSUE-511 実測）。
        # bid へ揃えるときは ISSUE-511 段階 1 の再生成と同時に本行を変える。
        price_basis="mid",
        # ベンダ（ISSUE-515 対策 2）。既存事実の明文化: ライブ tick バッファはこれまで
        # Dukascopy の配信（marketdata.fetch_ticks_since）だけから作られていた。
        vendor="dukascopy",
    ),
    # JP225 1分足（MT5 実時間ティック由来・原子）。実市場・ロールアップ経路。
    # ISSUE-447 段階 1・設計 §9 A-1（承認 2026-09-01）: **tick=False**。足内更新（forming_bar /
    # tf-period 供給）の MT5 対応は A-6＝未裁定の別段階であり、本段階はその経路に触れない。
    # 実体は設計 §5: <DATA_DIR>/jp225_mt5_m1.csv と rollups/jp225_mt5/。本行 1 つの削除で可逆。
    "jp225_mt5": DatasetDescriptor(
        path=DATA_DIR / "jp225_mt5_m1.csv",
        symbol="JP225",
        clamp_outliers=True,
        rollup=True,
        # ISSUE-512 段階 3 の準備（ISSUE-515 対策 1）。tick が False の間は tick_tree_token が
        # None を返すため読取経路は起動しない。木の枝名は marketdata.mt5_ticks.ingest.token_for
        # （JP225 + '@' + サーバ名）の値、基準は同 ingest.PRICE_BASIS（bid）と一致させる
        # （両者の一致は marketdata/tests/test_tick_price_basis_ledger.py が固定する）。
        tick_token="JP225@OANDA-Japan-MT5-Live",
        price_basis="bid",
        vendor="mt5",
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


class TickTokenMissing(ValueError):
    """台帳の記入漏れ（``tick`` が True なのに ``tick_token`` が未記入）専用の例外。

    素材（tick parquet）の torn-read / IO 失敗を握って継続する境界が本番経路に複数在り、
    いずれも包括的な ``except Exception`` である。記入漏れを素の ``ValueError`` で送ると
    その網に掛かり、WARNING と歯抜けへ化ける（落ちないぶん出力は形式上正しく、状態検証では
    原理的に検出できない）。**型を分ける**ことで、境界は「素材の失敗は握る／台帳の記入漏れは
    通す」を区別できる。

    ``ValueError`` の派生にしてあるのは、記入漏れを ``ValueError`` として捕捉している既存の
    呼び出し側の契約を変えないためである。逆に境界側で ``except ValueError: raise`` と
    書いてはならない。注入バーの破損（time 欄が非数値）も ``ValueError`` であり、そこまで
    貫通すると 1 つの素材破損で応答全体が落ちる。
    """


def tick_tree_token(ref: "str | None") -> "str | None":
    """``ref`` のティックがどの木の枝に在るかを台帳から引く（ISSUE-512 段階 1）。

    木の形の権威は marketdata/tick_tree.py、**どの枝か** の権威は本台帳である。読取側は
    marketdata/tf_meta.py の同名の窓口を経由して本関数の答えを受け取り、木の側が持つ
    既定引数には依存しない。

    Args:
        ref: datasetRef。台帳に無い ref も受ける（照会であって検証ではない）。

    Returns:
        ティック木の枝名。ティック木を持たない ref（``tick`` が False）と台帳に無い ref は
        ``None``。

    Raises:
        TickTokenMissing: ``tick`` が True なのに ``tick_token`` が未記入のとき（Fail-Stop）。
            **既定値へフォールバックしない**。落とさずに既定の枝名を返すと、新しい
            ティック ref が無言で Dukascopy の木を読む。読めてしまうぶん出力は形式上正しく、
            状態検証（値の正しさ）では原理的に検出できない。記入漏れはここで止める。
            ``ValueError`` の派生なので、既存の捕捉側の契約は変わらない。
    """
    d = REGISTRY.get(ref)
    if d is None or not d.tick:
        return None
    if d.tick_token is None:
        raise TickTokenMissing(
            f"datasetRef {ref!r} は tick=True ですが tick_token が未記入です。"
            " marketdata.dataset_registry.REGISTRY の当該記述子へ、読むべきティック木の枝名"
            "（<DATA_DIR>/ticks/YYYY/MM/DD/<token>_ticks.parquet の <token>）を記入してください。"
        )
    return d.tick_token


def tick_price_basis(ref: "str | None") -> "str | None":
    """``ref`` のティックを畳む価格基準を台帳から引く（ISSUE-515 対策 1）。

    ティック木を持たない ref（``tick`` が False）と台帳に無い ref は ``None``。``tick`` が True の
    記述子は構築時に基準を必ず持つ（:class:`DatasetDescriptor` が拒否する）ため、ここで既定値へ
    落とす分岐は無い。
    """
    d = REGISTRY.get(ref)
    if d is None or not d.tick:
        return None
    return d.price_basis


def tick_vendor(ref: "str | None") -> "str | None":
    """``ref`` のティックをライブで受けるベンダを台帳から引く（ISSUE-515 対策 2）。

    ティック木を持たない ref（``tick`` が False）と台帳に無い ref は ``None``。``tick`` が True の
    記述子は構築時にベンダを必ず持つ（:class:`DatasetDescriptor` が拒否する）。
    """
    d = REGISTRY.get(ref)
    if d is None or not d.tick:
        return None
    return d.vendor


def price_basis_of_tick_token(token: str) -> str:
    """ティック木の枝名 ``token`` を畳む価格基準を台帳から引く（ISSUE-515 対策 1）。

    市場プロファイルは ref ではなく木の枝名でティックを読むため、木から基準を引く口が要る。
    同じ木を読む ref は同じ基準であることを ``marketdata/tests/test_tick_price_basis_ledger.py``
    が固定する（木から一意に決まる）。

    Raises:
        ValueError: 台帳に無い木（既定の mid へ落とさない。落とすと未登録の木が無言で mid に
            なり、確定足と半スプレッドずれても出力は形式上正しい）。
    """
    bases = {d.price_basis for d in REGISTRY.values() if d.tick_token == token}
    bases.discard(None)
    if len(bases) != 1:
        raise ValueError(
            f"ティック木 {token!r} の価格基準を台帳から一意に引けません（候補 {sorted(bases)}）。"
            " marketdata.dataset_registry.REGISTRY の記述子へ tick_token と price_basis を記入してください。"
        )
    return bases.pop()


__all__ = [
    "DatasetDescriptor",
    "REGISTRY",
    "TickTokenMissing",
    "whitelist",
    "clamp_refs",
    "rollup_refs",
    "tick_refs",
    "tick_tree_token",
    "tick_price_basis",
    "price_basis_of_tick_token",
    "tick_vendor",
]
