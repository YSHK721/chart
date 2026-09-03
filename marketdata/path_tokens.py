"""marketdata.path_tokens — 名前 → パス成分の変換規則の **唯一の実体**（ISSUE-479 F-1）。

なぜ marketdata が所有するのか:
    この規則の消費者は最下層である（``marketdata/mt5_ticks/ingest.py`` の ``token_for`` が
    tick 木のディレクトリ名を組む）。かつて実体は ``tools/capture_mt5_symbol_spec.py`` にあり、
    最下層が ``tools`` を import していた（層の逆流・循環 C-1）。実害は例外型に出ていた:
    sanitize が送出する ``CaptureError`` は ``tools`` の型なので ``tools/mt5_tick_watch.py`` の
    捕捉集合（SupplyUnavailable / Mt5SupplyError / WireError）をすり抜け、周期処理が
    トレースバックで exit 1 になっていた。所有権を下へ移し、``tools`` 側は
    **同一関数オブジェクトを再エクスポート**する（規則の第 2 実装を作らない）。

依存ゼロ（重要）:
    本モジュールは **import 文を 1 つも持たない**。最下層から参照できることが移設の目的で
    あり、ここに依存が入ると目的が崩れる。``marketdata/tests/test_path_tokens.py`` が AST で
    強制する。この性質はもう 1 つの用途も支える: ``tools/capture_mt5_symbol_spec.py`` は
    リポジトリごとではなく**ファイル単位で Windows VM へ持ち込む**運用であり、本ファイルを
    capture 本体の隣へ写して 2 ファイルで配布する（依存ゼロでなければ写しが動かない）。
"""

#: パス成分に許すのは ASCII 英数と ``.`` ``_`` ``-`` のみ。それ以外は 1 文字 1 文字を
#: ``-`` へ機械的に置換する（1 文字 → 1 文字。長さは変えない）。
#: 例: ``OANDA-Japan MT5 Live`` → ``OANDA-Japan-MT5-Live`` / ``a/b\\c`` → ``a-b-c``。
#: 置換後が空白のみ・``.``・``..`` になる場合は、親ディレクトリへ逃げる経路になるため中断する。
_SAFE_CHARS = frozenset(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-"
)
_REPLACEMENT = "-"


class PathTokenError(ValueError):
    """パス成分として使えない値を渡されたことを表す（Fail-Stop）。

    ``ValueError`` 系にするのは、これが **入力値の異常**だからである（実行環境の異常ではない）。
    上位で捕捉して各層の失敗型へ翻訳する（``ingest.token_for`` → ``Mt5SupplyError``）。
    """


def sanitize_path_component(raw: str) -> str:
    """サーバ名・銘柄名をファイルパス成分へ機械的に変換する（規則は ``_SAFE_CHARS`` を参照）。"""
    text = "" if raw is None else str(raw)
    converted = "".join(c if c in _SAFE_CHARS else _REPLACEMENT for c in text)
    if not text.strip() or converted in (".", ".."):
        raise PathTokenError(
            f"パス成分として使えない値です: {raw!r} → {converted!r}。"
            " 供給元の server / symbol を確認してください。"
        )
    return converted
