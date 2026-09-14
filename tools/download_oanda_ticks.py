"""OANDA 証券マイページの「MT5 用ティックデータ」を一括取得する CLI（取得のみ）。

手動 UI（銘柄を選ぶ → 年/月を選ぶ → ダウンロード）を 1 コマンドへ置換する。
UI と同じ 2 段の HTTP 呼出をそのまま自動化するだけであり、独自の推測は持たない。

  1. ``/trade/web/tools/tickDownload`` を取得し、ページに**埋め込まれている**アーカイブ一覧
     （``{ file: "ticks_JP225_2020-05.zip", pair: "JP225", year: "2020", month: "5" }``）を読む。
  2. 各 file について ``/trade/web/tools/tickDownload/generateURL?fileName=<file>`` を叩き、
     返る JSON の ``url``（署名付き一時 URL）から zip を保存する。

**取得可能な月の一覧は本スクリプトに書かない**（人が値を選ばない・書かない＝ISSUE-445 恒久策と同型）。
一覧の権威はページ自身であり、月が増えれば次回実行で自動的に増える。

認証:
    マイページのログインセッション Cookie が必須（未ログインではページに一覧が出ない）。
    ブラウザは host（macOS）側・本スクリプトは container 側で動くため、**host に保存した
    ファイルは container から見えない**。よって既定の受け口は **stdin への貼り付け** とする。

    **最短手順（Chrome / macOS）**:
      1. ログイン済みで https://www.oanda.jp/trade/web/tools/tickDownload を開く
      2. ``⌥⌘I`` で DevTools を開き **Network** タブ → ``⌘R`` で再読込
      3. 一覧の一番上の行 ``tickDownload`` を右クリック → **Copy** → **Copy as cURL**
      4. container のターミナルで ``python -m tools.download_oanda_ticks --cookie-stdin``
         を実行し、``⌘V`` で貼り付け → Enter → ``Ctrl-D``

    貼り付けた cURL から cookie を自動抽出する。``Cookie: a=1; b=2`` 形式や Netscape
    cookies.txt も同じ経路で受け付ける（自動判別）。``--cookie-file`` / 環境変数
    ``OANDA_COOKIE`` も使える。**Cookie をリポジトリへ置かないこと**。

保存:
    既定は ``DATA_DIR/oanda_ticks/<PAIR>/ticks_<PAIR>_<YYYY-MM>.zip``（gitignore 対象）。
    既存ファイルは既定で**上書きしない**（スキップ。``--force`` 指定時のみ再取得）。
    ダウンロードは ``.part`` へ書いてから ``os.replace`` する（途中終了で壊れた zip を残さない）。
    取得結果は ``manifest.json``（sha256 / bytes / 取得時刻 UTC）へ追記し、後段の不変性検証に使う。

本スクリプトが**しないこと**（取得と解釈を分離する）:
    - CSV の解釈・時刻正規化・parquet 化・tick 木への配置（ISSUE-447 段階 2 以降の仕事）。
    - 生ティック列（RAW_COLUMNS）や tick 木レイアウトの再定義（tools は合成点＝重複を持たない）。

zip 内 CSV の時刻について（取得側の注意・実測 2026-08-27）:
    列は ``<DATE> <TIME> <BID> <ASK> <LAST> <VOLUME>``（TAB 区切り）で、``LAST``/``VOLUME`` は空。
    時刻は **MT5 サーバ時刻**（ページ表記「日本時間 1 日 0:00 開始 = MT5 前月末日 17:00、
    夏時間は 18:00」＝冬 UTC+2 / 夏 UTC+3）。``ticks_JP225_2020-05.zip`` の先頭行は
    ``2020.04.30 18:00:00.613``（= JST 2020-05-01 00:00）で、この表記と一致する。
    **UTC 正規化は取り込み側の責務**であり、ここでは 1 バイトも変換しない。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import re
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from http.cookies import SimpleCookie
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence
from zipfile import BadZipFile, ZipFile

LOG = logging.getLogger("download_oanda_ticks")

BASE = "https://www.oanda.jp"
PAGE_URL = f"{BASE}/trade/web/tools/tickDownload"
GENERATE_URL = f"{PAGE_URL}/generateURL"

# ページ埋め込みの archives 要素。UI の JS が読むのと同一の literal を読む。
_ARCHIVE_RE = re.compile(
    r'\{\s*file:\s*"(?P<file>[^"]+)"\s*,'
    r'\s*pair:\s*"(?P<pair>[^"]+)"\s*,'
    r'\s*year:\s*"(?P<year>\d{4})"\s*,'
    r'\s*month:\s*"(?P<month>\d{1,2})"\s*\}'
)

_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36"
)


class AuthError(RuntimeError):
    """ログインセッションが無効（またはページ構造が変わった）ことを示す。"""


@dataclass(frozen=True)
class Archive:
    """ページが提供する 1 アーカイブ（銘柄 × 年月）。"""

    file: str
    pair: str
    year: int
    month: int

    @property
    def ym(self) -> str:
        return f"{self.year:04d}-{self.month:02d}"


# ---------------------------------------------------------------------------
# 純粋関数（ネットワーク非依存・単体で検定できる）
# ---------------------------------------------------------------------------

def parse_archives(html: str) -> List[Archive]:
    """ページ HTML から埋め込みアーカイブ一覧を読む。

    1 件も取れない場合は :class:`AuthError`（未ログイン時はログイン画面が返るため）。
    """
    found = [
        Archive(m.group("file"), m.group("pair"), int(m.group("year")), int(m.group("month")))
        for m in _ARCHIVE_RE.finditer(html)
    ]
    if not found:
        raise AuthError(
            "ページにアーカイブ一覧が見つかりません。Cookie が失効している（ログイン画面が"
            "返っている）か、ページ構造が変わった可能性があります。"
        )
    return found


def parse_ym(text: str) -> "tuple[int, int]":
    """``YYYY-MM`` / ``YYYY/MM`` を (year, month) へ。"""
    m = re.fullmatch(r"(\d{4})[-/](\d{1,2})", text.strip())
    if not m:
        raise ValueError(f"年月の書式が不正です（YYYY-MM を指定してください）: {text!r}")
    year, month = int(m.group(1)), int(m.group(2))
    if not 1 <= month <= 12:
        raise ValueError(f"月が範囲外です: {text!r}")
    return year, month


def select_archives(
    archives: Iterable[Archive],
    pairs: Sequence[str],
    ym_from: "Optional[tuple[int, int]]" = None,
    ym_to: "Optional[tuple[int, int]]" = None,
) -> List[Archive]:
    """銘柄と年月範囲で絞り、(銘柄, 年, 月) 昇順で返す。範囲は両端を含む。"""
    wanted = {p.upper() for p in pairs}
    out = [a for a in archives if a.pair.upper() in wanted]
    if ym_from is not None:
        out = [a for a in out if (a.year, a.month) >= ym_from]
    if ym_to is not None:
        out = [a for a in out if (a.year, a.month) <= ym_to]
    return sorted(out, key=lambda a: (a.pair, a.year, a.month))


# Chrome の「Copy as cURL」が出す cookie ヘッダ（``-H $'cookie: …'`` / ``-b '…'`` の双方）。
_CURL_COOKIE_RE = re.compile(r"""(?:-H|--header)\s+\$?(['"])\s*cookie:\s*(?P<v>.*?)(?<!\\)\1"""
                             r"""|(?:-b|--cookie)\s+\$?(['"])(?P<v2>.*?)(?<!\\)\3""",
                             re.IGNORECASE | re.DOTALL)


def _unescape_shell_single_quoted(value: str) -> str:
    """``$'…'`` 内のエスケープを戻す（Chrome は ``\\'``・``\\\\`` を使う）。"""
    return value.replace("\\'", "'").replace("\\\\", "\\")


def cookie_header_from_text(text: str) -> str:
    """Cookie ファイルの中身を ``Cookie:`` ヘッダ値へ正規化する。

    受け付ける形式（自動判別）:
      - Chrome DevTools の「Copy as cURL」をそのまま貼ったもの（**推奨・最短**）
      - ``Cookie: a=1; b=2`` / ``a=1; b=2``（Request Headers からのコピー）
      - Netscape cookies.txt（TAB 区切り 7 列・``#`` 始まりはコメント）
    """
    if "curl " in text:
        m = _CURL_COOKIE_RE.search(text)
        if m:
            raw = m.group("v") if m.group("v") is not None else m.group("v2")
            return _unescape_shell_single_quoted(raw).strip()
        raise ValueError(
            "cURL は見つかりましたが cookie ヘッダがありません。"
            "ログイン済みのタブで Copy as cURL し直してください。"
        )

    lines = [ln for ln in text.splitlines() if ln.strip()]
    data_lines = [ln for ln in lines if not ln.lstrip().startswith("#")]
    netscape = [ln for ln in data_lines if ln.count("\t") >= 6]
    if netscape:
        pairs = []
        for ln in netscape:
            cols = ln.split("\t")
            name, value = cols[5].strip(), cols[6].strip()
            if name:
                pairs.append(f"{name}={value}")
        if not pairs:
            raise ValueError("Netscape cookies.txt から Cookie を 1 件も読めませんでした。")
        return "; ".join(pairs)

    raw = " ".join(data_lines).strip()
    if raw.lower().startswith("cookie:"):
        raw = raw.split(":", 1)[1].strip()
    if "=" not in raw:
        raise ValueError(
            "Cookie の書式を判別できません（``名前=値; 名前=値`` か cookies.txt を渡してください）。"
        )
    # 妥当性の最低確認（壊れた文字列で 401 を踏み続けない）。
    jar = SimpleCookie()
    jar.load(raw)
    if not jar:
        raise ValueError("Cookie を 1 件も解釈できませんでした。")
    return raw


def load_cookie_header(
    cookie_file: Optional[str],
    env: "Optional[Dict[str, str]]" = None,
    stdin_text: Optional[str] = None,
) -> str:
    """貼り付け（stdin）・ファイル・環境変数のいずれかから Cookie ヘッダ値を得る。

    ブラウザは host（macOS）側、本スクリプトは container 側で動くため、host に保存した
    ファイルは container から見えない。既定の受け口を **stdin への貼り付け** とする。
    """
    env = os.environ if env is None else env
    if stdin_text is not None:
        return cookie_header_from_text(stdin_text)
    if cookie_file:
        return cookie_header_from_text(Path(cookie_file).read_text(encoding="utf-8"))
    raw = env.get("OANDA_COOKIE")
    if raw:
        return cookie_header_from_text(raw)
    raise AuthError(
        "Cookie が指定されていません。--cookie-stdin を付けて Copy as cURL を貼り付けるか、"
        "--cookie-file / 環境変数 OANDA_COOKIE を使ってください。"
    )


def dest_path(out_dir: Path, archive: Archive) -> Path:
    """保存先（銘柄ごとのサブディレクトリ）。ファイル名はページの file 名をそのまま使う。"""
    return out_dir / archive.pair.upper() / archive.file


def sha256_of(path: Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        while True:
            block = fh.read(chunk)
            if not block:
                break
            h.update(block)
    return h.hexdigest()


def verify_zip(path: Path) -> List[str]:
    """zip の健全性を検査し、内包エントリ名を返す。壊れていれば例外。"""
    with ZipFile(path) as zf:
        broken = zf.testzip()
        if broken is not None:
            raise BadZipFile(f"zip 内の {broken} が壊れています: {path}")
        names = zf.namelist()
    if not names:
        raise BadZipFile(f"zip が空です: {path}")
    return names


# ---------------------------------------------------------------------------
# HTTP（差し替え可能な境界。テストは Fetcher を注入して網羅する）
# ---------------------------------------------------------------------------

class RequestsFetcher:
    """``requests`` を隔離する adapter（既定実装）。"""

    def __init__(self, cookie_header: str, timeout: float = 60.0) -> None:
        import requests  # 既存依存。トップレベル import しない（DI 差替時に不要なため）。

        self._session = requests.Session()
        self._session.headers.update({
            "User-Agent": _USER_AGENT,
            "Accept-Language": "ja,en;q=0.9",
            "Cookie": cookie_header,
        })
        self._timeout = timeout

    def get_text(self, url: str) -> str:
        r = self._session.get(url, timeout=self._timeout)
        r.raise_for_status()
        return r.text

    def get_json(self, url: str, referer: str) -> dict:
        r = self._session.get(
            url,
            timeout=self._timeout,
            headers={"X-Requested-With": "XMLHttpRequest", "Referer": referer,
                     "Accept": "application/json, text/javascript, */*; q=0.01"},
        )
        r.raise_for_status()
        try:
            return r.json()
        except ValueError as exc:  # ログイン画面（HTML）が返った等。
            raise AuthError(
                f"generateURL が JSON を返しませんでした（Cookie 失効の可能性）: {url}"
            ) from exc

    def download(self, url: str, dest: Path, chunk: int = 1 << 20) -> int:
        """署名付き URL を ``dest`` へ保存し、バイト数を返す。Cookie は送らない。"""
        import requests

        written = 0
        with requests.get(url, stream=True, timeout=self._timeout,
                          headers={"User-Agent": _USER_AGENT}) as r:
            r.raise_for_status()
            with dest.open("wb") as fh:
                for block in r.iter_content(chunk_size=chunk):
                    if block:
                        fh.write(block)
                        written += len(block)
        return written


def _with_retry(func, retries: int, backoff: float, what: str):
    """一時障害のみ再試行する。:class:`AuthError` は即時失敗（叩き続けない）。"""
    last: Optional[BaseException] = None
    for attempt in range(1, retries + 1):
        try:
            return func()
        except AuthError:
            raise
        except Exception as exc:  # noqa: BLE001 - 通信層の例外種別はベンダ依存
            last = exc
            if attempt == retries:
                break
            wait = backoff * (2 ** (attempt - 1))
            LOG.warning("%s に失敗（%d/%d）: %s / %.1fs 後に再試行", what, attempt, retries, exc, wait)
            time.sleep(wait)
    assert last is not None
    raise last


# ---------------------------------------------------------------------------
# manifest（取得済みの実測記録。後段の不変性検証に使う）
# ---------------------------------------------------------------------------

def load_manifest(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        LOG.warning("manifest が壊れているため無視します: %s", path)
        return {}


def save_manifest(path: Path, manifest: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".part")
    tmp.write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                   encoding="utf-8")
    os.replace(tmp, path)


# ---------------------------------------------------------------------------
# 取得本体
# ---------------------------------------------------------------------------

def fetch_one(fetcher, archive: Archive, dest: Path, retries: int, backoff: float) -> "tuple[int, str, List[str]]":
    """1 アーカイブを取得して (bytes, sha256, zip 内エントリ名) を返す。"""
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_suffix(dest.suffix + ".part")

    payload = _with_retry(
        lambda: fetcher.get_json(f"{GENERATE_URL}?fileName={archive.file}", PAGE_URL),
        retries, backoff, f"generateURL({archive.file})",
    )
    url = payload.get("url")
    if not url:
        raise AuthError(f"generateURL の応答に url がありません: {archive.file} / {payload!r}")

    size = _with_retry(lambda: fetcher.download(url, part), retries, backoff, f"download({archive.file})")
    try:
        names = verify_zip(part)
        digest = sha256_of(part)
    except Exception:
        part.unlink(missing_ok=True)
        raise
    os.replace(part, dest)
    return size, digest, names


def run(args: argparse.Namespace, fetcher=None, stdin=None) -> int:
    out_dir = Path(args.out_dir)
    if fetcher is None:
        stdin_text = None
        if getattr(args, "cookie_stdin", False):
            stream = sys.stdin if stdin is None else stdin
            print("Copy as cURL を貼り付けて Enter → Ctrl-D:", file=sys.stderr)
            stdin_text = stream.read()
        fetcher = RequestsFetcher(load_cookie_header(args.cookie_file, stdin_text=stdin_text))

    html = _with_retry(lambda: fetcher.get_text(PAGE_URL), args.retries, args.backoff, "tickDownload ページ取得")
    archives = parse_archives(html)
    LOG.info("ページが提供するアーカイブ: %d 件 / 銘柄 %s",
             len(archives), sorted({a.pair for a in archives}))

    ym_from = parse_ym(args.since) if args.since else None
    ym_to = parse_ym(args.until) if args.until else None
    targets = select_archives(archives, args.symbol, ym_from, ym_to)
    if args.limit:
        targets = targets[: args.limit]
    if not targets:
        LOG.error("条件に一致するアーカイブがありません（銘柄=%s since=%s until=%s）",
                  args.symbol, args.since, args.until)
        return 1

    if args.list:
        for a in targets:
            print(f"{a.pair}\t{a.ym}\t{a.file}")
        return 0

    manifest_path = out_dir / "manifest.json"
    manifest = load_manifest(manifest_path)

    done = skipped = failed = 0
    for i, a in enumerate(targets, 1):
        dest = dest_path(out_dir, a)
        if dest.exists() and not args.force:
            LOG.info("[%d/%d] skip（既存）: %s", i, len(targets), dest)
            skipped += 1
            continue
        if args.dry_run:
            LOG.info("[%d/%d] dry-run: %s -> %s", i, len(targets), a.file, dest)
            skipped += 1
            continue
        try:
            size, digest, names = fetch_one(fetcher, a, dest, args.retries, args.backoff)
        except AuthError:
            raise
        except Exception as exc:  # noqa: BLE001
            LOG.error("[%d/%d] 失敗: %s: %s", i, len(targets), a.file, exc)
            failed += 1
            continue
        prev = manifest.get(a.file)
        if prev and prev.get("sha256") != digest:
            LOG.warning("内容が前回と異なります（再配信・修正の可能性）: %s %s -> %s",
                        a.file, prev.get("sha256", "")[:16], digest[:16])
        manifest[a.file] = {
            "pair": a.pair, "ym": a.ym, "bytes": size, "sha256": digest,
            "entries": names,
            "downloaded_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        save_manifest(manifest_path, manifest)
        LOG.info("[%d/%d] ok: %s (%.1f MB, sha256=%s…)",
                 i, len(targets), dest.name, size / (1 << 20), digest[:16])
        done += 1
        if i < len(targets) and args.sleep > 0:
            time.sleep(args.sleep)

    LOG.info("完了: 取得 %d / skip %d / 失敗 %d（保存先 %s）", done, skipped, failed, out_dir)
    return 1 if failed else 0


def build_parser() -> argparse.ArgumentParser:
    from marketdata.paths import DATA_DIR  # 保存先の基点は単一権威から採る。

    p = argparse.ArgumentParser(
        prog="download_oanda_ticks",
        description="OANDA 証券マイページの MT5 用ティックデータ（月別 zip）を一括取得する。",
    )
    p.add_argument("--symbol", action="append", default=None,
                   help="銘柄（複数可）。既定 JP225。例: --symbol JP225 --symbol US500")
    p.add_argument("--since", default="2020-05", help="開始年月 YYYY-MM（既定 2020-05・両端含む）")
    p.add_argument("--until", default=None, help="終了年月 YYYY-MM（既定: ページの最新月まで）")
    p.add_argument("--out-dir", default=str(DATA_DIR / "oanda_ticks"), help="保存先ディレクトリ")
    p.add_argument("--cookie-stdin", action="store_true",
                   help="Copy as cURL の貼り付けを標準入力から受け取る（**推奨**・ファイル不要）")
    p.add_argument("--cookie-file", default=None,
                   help="Chrome の Copy as cURL を貼ったファイル（Cookie ヘッダ値・cookies.txt も可。"
                        "既定は環境変数 OANDA_COOKIE）")
    p.add_argument("--sleep", type=float, default=2.0, help="連続取得の間隔秒（既定 2.0）")
    p.add_argument("--retries", type=int, default=3, help="一時障害の再試行回数（既定 3）")
    p.add_argument("--backoff", type=float, default=2.0, help="再試行の初期待機秒（既定 2.0・指数増加）")
    p.add_argument("--force", action="store_true", help="既存ファイルも再取得する（既定はスキップ）")
    p.add_argument("--dry-run", action="store_true", help="取得せず対象だけを表示する")
    p.add_argument("--list", action="store_true", help="取得可能な対象を一覧表示して終了する")
    p.add_argument("--limit", type=int, default=None, help="先頭 N 件だけ処理する（動作確認用）")
    p.add_argument("--verbose", action="store_true", help="DEBUG ログを出す")
    return p


def main(argv: "Optional[Sequence[str]]" = None) -> int:
    args = build_parser().parse_args(argv)
    if args.symbol is None:
        args.symbol = ["JP225"]
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    try:
        return run(args)
    except AuthError as exc:
        LOG.error("認証エラー: %s", exc)
        return 2
    except ValueError as exc:  # Cookie の書式不正など、利用者が直せる入力エラー。
        LOG.error("入力エラー: %s", exc)
        return 2


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
