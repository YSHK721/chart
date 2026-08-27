"""``tools/download_oanda_ticks.py`` の検定（ネットワーク非依存）。

ネットワーク境界は Fetcher 差替で塞ぎ、次を固定する:
  - 取得可能月の一覧は**ページの埋め込み literal から読む**（スクリプトに月を書かない）
  - 未ログイン（一覧 0 件・JSON でない応答）は再試行せず AuthError で即時失敗する
  - 既存ファイルは既定で上書きしない（``--force`` のときだけ再取得する）
  - 壊れた zip を最終ファイルとして残さない（``.part`` を捨てる）
"""
from __future__ import annotations

import io
import json
from pathlib import Path
from zipfile import ZipFile

import pytest

from tools import download_oanda_ticks as dl

# 実ページ（www.oanda.jp/trade/web/tools/tickDownload）の埋め込みと同形。
PAGE = """
    var archives = [
          { file: "ticks_JP225_2020-05.zip", pair: "JP225", year: "2020", month: "5" },
          { file: "ticks_JP225_2020-06.zip", pair: "JP225", year: "2020", month: "6" },
          { file: "ticks_JP225_2026-08.zip", pair: "JP225", year: "2026", month: "8" },
          { file: "ticks_US500_2020-05.zip", pair: "US500", year: "2020", month: "5" },
    ];
"""


def _zip_bytes(name: str = "ticks_JP225_2020-05.csv", body: bytes = b"<DATE>\t<TIME>\n") -> bytes:
    buf = io.BytesIO()
    with ZipFile(buf, "w") as zf:
        zf.writestr(name, body)
    return buf.getvalue()


class FakeFetcher:
    """generateURL → 署名付き URL → zip 本体、の 3 段をメモリ上で再現する。"""

    def __init__(self, page: str = PAGE, payload: bytes | None = None):
        self._page = page
        self._payload = _zip_bytes() if payload is None else payload
        self.generated: list[str] = []
        self.downloaded: list[str] = []

    def get_text(self, url: str) -> str:
        return self._page

    def get_json(self, url: str, referer: str) -> dict:
        self.generated.append(url)
        return {"response": "ok", "url": f"https://example.invalid/signed/{url.rsplit('=', 1)[1]}"}

    def download(self, url: str, dest: Path, chunk: int = 1 << 20) -> int:
        self.downloaded.append(url)
        dest.write_bytes(self._payload)
        return len(self._payload)


def _args(tmp_path: Path, **over):
    ns = dl.build_parser().parse_args([])
    ns.symbol = ["JP225"]
    ns.out_dir = str(tmp_path)
    ns.sleep = 0.0
    for k, v in over.items():
        setattr(ns, k, v)
    return ns


# --- 純粋関数 ---------------------------------------------------------------

def test_parse_archives_reads_embedded_list():
    got = dl.parse_archives(PAGE)
    assert [(a.pair, a.ym) for a in got] == [
        ("JP225", "2020-05"), ("JP225", "2020-06"), ("JP225", "2026-08"), ("US500", "2020-05"),
    ]


def test_parse_archives_without_list_is_auth_error():
    """未ログインではログイン画面が返り一覧が無い。推測で続行しない。"""
    with pytest.raises(dl.AuthError):
        dl.parse_archives("<html><body>ログイン</body></html>")


def test_select_archives_filters_symbol_and_range():
    archives = dl.parse_archives(PAGE)
    got = dl.select_archives(archives, ["JP225"], (2020, 6), (2026, 8))
    assert [a.ym for a in got] == ["2020-06", "2026-08"]
    assert all(a.pair == "JP225" for a in got)


def test_select_archives_range_is_inclusive_on_both_ends():
    archives = dl.parse_archives(PAGE)
    got = dl.select_archives(archives, ["JP225"], (2020, 5), (2020, 6))
    assert [a.ym for a in got] == ["2020-05", "2020-06"]


@pytest.mark.parametrize("text,expected", [
    ("Cookie: SESSION=abc; other=1", "SESSION=abc; other=1"),
    ("SESSION=abc; other=1", "SESSION=abc; other=1"),
    ("# Netscape HTTP Cookie File\n"
     ".oanda.jp\tTRUE\t/\tTRUE\t0\tSESSION\tabc\n"
     ".oanda.jp\tTRUE\t/\tTRUE\t0\tother\t1\n", "SESSION=abc; other=1"),
])
def test_cookie_header_from_text(text, expected):
    assert dl.cookie_header_from_text(text) == expected


def test_cookie_header_rejects_garbage():
    with pytest.raises(ValueError):
        dl.cookie_header_from_text("これは Cookie ではありません")


def test_load_cookie_header_requires_a_source():
    with pytest.raises(dl.AuthError):
        dl.load_cookie_header(None, env={})


def test_parse_ym_rejects_bad_format():
    assert dl.parse_ym("2020-05") == (2020, 5)
    with pytest.raises(ValueError):
        dl.parse_ym("2020-13")
    with pytest.raises(ValueError):
        dl.parse_ym("202005")


# --- 取得本体 ---------------------------------------------------------------

def test_run_downloads_and_records_manifest(tmp_path):
    fetcher = FakeFetcher()
    rc = dl.run(_args(tmp_path, since="2020-05", until="2020-06"), fetcher=fetcher)
    assert rc == 0

    saved = sorted(p.name for p in (tmp_path / "JP225").glob("*.zip"))
    assert saved == ["ticks_JP225_2020-05.zip", "ticks_JP225_2020-06.zip"]
    assert not list(tmp_path.rglob("*.part")), ".part を残さない"

    manifest = json.loads((tmp_path / "manifest.json").read_text(encoding="utf-8"))
    assert set(manifest) == set(saved)
    entry = manifest["ticks_JP225_2020-05.zip"]
    assert entry["pair"] == "JP225" and entry["ym"] == "2020-05"
    assert len(entry["sha256"]) == 64 and entry["bytes"] > 0
    assert entry["entries"] == ["ticks_JP225_2020-05.csv"]


def test_run_skips_existing_by_default_and_refetches_with_force(tmp_path):
    fetcher = FakeFetcher()
    dl.run(_args(tmp_path, since="2020-05", until="2020-05"), fetcher=fetcher)
    assert len(fetcher.downloaded) == 1

    dl.run(_args(tmp_path, since="2020-05", until="2020-05"), fetcher=fetcher)
    assert len(fetcher.downloaded) == 1, "既存ファイルは既定で再取得しない"

    dl.run(_args(tmp_path, since="2020-05", until="2020-05", force=True), fetcher=fetcher)
    assert len(fetcher.downloaded) == 2, "--force のときだけ再取得する"


def test_run_dry_run_does_not_touch_network_or_disk(tmp_path):
    fetcher = FakeFetcher()
    rc = dl.run(_args(tmp_path, since="2020-05", dry_run=True), fetcher=fetcher)
    assert rc == 0
    assert fetcher.downloaded == [] and fetcher.generated == []
    assert not list(tmp_path.rglob("*.zip"))


def test_run_aborts_immediately_when_generate_url_is_not_json(tmp_path):
    class Expired(FakeFetcher):
        def get_json(self, url, referer):
            raise dl.AuthError("JSON ではありません")

    with pytest.raises(dl.AuthError):
        dl.run(_args(tmp_path, since="2020-05"), fetcher=Expired())


def test_broken_zip_is_not_left_behind(tmp_path):
    fetcher = FakeFetcher(payload=b"not a zip at all")
    rc = dl.run(_args(tmp_path, since="2020-05", until="2020-05"), fetcher=fetcher)
    assert rc == 1, "失敗は終了コードへ出す"
    assert not list(tmp_path.rglob("*.zip")) and not list(tmp_path.rglob("*.part"))
    assert not (tmp_path / "manifest.json").exists()


def test_manifest_change_is_reported_not_silently_overwritten(tmp_path, caplog):
    """過去月の内容が変わったら警告する（当社は予告なくデータを修正しうる）。"""
    dl.run(_args(tmp_path, since="2020-05", until="2020-05"), fetcher=FakeFetcher())
    other = FakeFetcher(payload=_zip_bytes(body=b"<DATE>\t<TIME>\ndifferent\n"))
    with caplog.at_level("WARNING"):
        dl.run(_args(tmp_path, since="2020-05", until="2020-05", force=True), fetcher=other)
    assert any("内容が前回と異なります" in r.getMessage() for r in caplog.records)


def test_list_mode_prints_targets_without_fetching(tmp_path, capsys):
    fetcher = FakeFetcher()
    rc = dl.run(_args(tmp_path, since="2020-05", list=True), fetcher=fetcher)
    assert rc == 0
    out = capsys.readouterr().out.strip().splitlines()
    assert out[0].split("\t") == ["JP225", "2020-05", "ticks_JP225_2020-05.zip"]
    assert fetcher.downloaded == []
