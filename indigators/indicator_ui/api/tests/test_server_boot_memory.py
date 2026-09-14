"""サーバ起動時メモリ有界の回帰テスト（OOM 退行ガード・ISSUE-012 系）。

本機能の根本要因は server が 4.5M 行 / 284MB の 1 分足を起動時に全ロードして OOM/Killed に
なることだった。修正後はロールアップ＋tail 読みで「起動時に全件をロードしない」。本テストは
その**設計不変条件**を検証する: サーバを起動し /candles を一度も叩かない（=データ未要求）状態で
常駐 RSS が閾値未満であること。全件ロードが復活すると df ≈489MB で RSS が数百 MB へ跳ね FAIL する。

設計（非 flaky）:
  - 計測対象は絶対 ms ではなく「リクエスト前の常駐 RSS」。時間アサーションは環境・負荷で揺れ
    flaky になるため採らない。RSS 上限は退行（全件ロード ≈500MB+）と baseline（実測 ~68MB）の
    広い間隙に置く（``_RSS_CEILING_MB`` = 250）。
  - サーバ殻は子プロセスとして隔離起動し（in-process では pytest 自身の RSS と混ざり計測不能）、
    listen 直後・リクエスト前に ``/proc/<pid>/status`` の VmRSS を読む。
  - Linux/``/proc`` 前提のため非対応環境は skip する。
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

# 起動時（リクエスト前）常駐 RSS の上限。baseline 実測 ~68MB に対し広い余裕を取りつつ、
# 全件ロード退行（df ≈489MB → RSS 数百 MB）は確実に上回る位置に置く（非 flaky）。
_RSS_CEILING_MB = 250
# /candles 配信後の常駐 RSS 上限。1m=tail / 5m=ロールアップ(64MB) ロード後 実測 ~143MB に対し
# 余裕を取りつつ、配信経路で全件ロードが復活する退行（500MB+）は確実に上回る（非 flaky）。
_RSS_CEILING_SERVING_MB = 300
# import（pandas/adapter/埋め込み R 解決）込みの listen 到達待ち上限。遅い CI でも十分。
_BOOT_TIMEOUT_S = 60.0

_API_ROOT = Path(__file__).resolve().parents[1]
_SERVER_PY = _API_ROOT / "framework" / "server.py"

pytestmark = pytest.mark.skipif(
    sys.platform != "linux" or not Path("/proc").is_dir(),
    reason="VmRSS 計測は Linux/proc 前提",
)


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _is_listening(port: int) -> bool:
    s = socket.socket()
    s.settimeout(0.3)
    try:
        s.connect(("127.0.0.1", port))
        s.close()
        return True
    except OSError:
        return False


def _rss_mb(pid: int) -> float:
    with open(f"/proc/{pid}/status", encoding="utf-8") as f:
        for line in f:
            if line.startswith("VmRSS"):
                return int(line.split()[1]) / 1024  # kB → MB
    raise AssertionError("VmRSS が /proc/<pid>/status に見つからない")


def _spawn_server(port: int) -> subprocess.Popen:
    """server.py を子プロセスで起動し、listen 到達まで待って返す（隔離 RSS 計測用）。"""
    proc = subprocess.Popen(
        [sys.executable, str(_SERVER_PY), "--port", str(port)],
        cwd=str(_API_ROOT),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    deadline = time.monotonic() + _BOOT_TIMEOUT_S
    while not _is_listening(port):
        if proc.poll() is not None:
            raise AssertionError(f"サーバが起動に失敗した（exit={proc.returncode}）")
        if time.monotonic() > deadline:
            raise AssertionError(f"{_BOOT_TIMEOUT_S}s 以内に listen しなかった")
        time.sleep(0.01)
    return proc


def _terminate(proc: subprocess.Popen) -> None:
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()


def test_server_boot_rss_is_bounded_without_loading_full_dataset():
    """起動直後（/candles 未要求）の常駐 RSS が上限未満 = 全件ロードを起動時に行わない。

    回帰: 起動時に 1 分足全件をロードする実装へ戻ると df ≈489MB で RSS が閾値を超え FAIL する。
    """
    port = _free_port()
    proc = _spawn_server(port)
    try:
        # listen 直後・リクエスト前の常駐 RSS（データ未ロードであることの証跡）。
        rss = _rss_mb(proc.pid)
        assert rss < _RSS_CEILING_MB, (
            f"起動時 RSS {rss:.0f}MB が上限 {_RSS_CEILING_MB}MB を超過。"
            f"起動時に全件データをロードしていないか確認せよ（OOM 退行の疑い）。"
        )
    finally:
        _terminate(proc)


def _get_candles(port: int, qs: str) -> int:
    """/candles?<qs> を叩き HTTP status を返す（HTTPError も status へ正規化）。"""
    url = f"http://127.0.0.1:{port}/candles?{qs}"
    try:
        with urllib.request.urlopen(url, timeout=60) as r:
            r.read()
            return r.status
    except urllib.error.HTTPError as exc:
        return exc.code


def test_server_rss_is_bounded_after_serving_candles():
    """jp225_m1 を 1m（tail）＋5m（ロールアップ）配信した後も常駐 RSS が上限未満。

    配信経路で 1 分足全件をロードする退行が入ると RSS が数百 MB へ跳ね FAIL する。
    jp225_m1 の実データ（gitignore・非再配布）が未配置の環境では skip する。
    """
    port = _free_port()
    proc = _spawn_server(port)
    try:
        # データ可用性プローブ。未配置（200 以外）なら skip（CI ではデータ非配置）。
        if _get_candles(port, "datasetRef=jp225_m1&timeframe=5m&limit=10") != 200:
            pytest.skip("jp225_m1 実データ未配置のため配信 RSS テストを skip")
        # 1m（tail）と 5m（ロールアップ）を配信させ、データ読みを経た RSS を測る。
        assert _get_candles(port, "datasetRef=jp225_m1&limit=500") == 200
        assert _get_candles(port, "datasetRef=jp225_m1&timeframe=5m&limit=500") == 200
        rss = _rss_mb(proc.pid)
        assert rss < _RSS_CEILING_SERVING_MB, (
            f"配信後 RSS {rss:.0f}MB が上限 {_RSS_CEILING_SERVING_MB}MB を超過。"
            f"配信経路で全件ロードが復活していないか確認せよ（OOM 退行の疑い）。"
        )
    finally:
        _terminate(proc)
