"""dataset.load_dataframe の resample 結果キャッシュ（A’・性能最適化）の検証。

設計入力: resample キャッシュ仕様（P-1 base mtime 単一真実源 / P-2 plain dict 上書き有界）。
`/compute`・`/candles` が毎回 4.5M 行を上位足へ resample する重複を消すため、
`load_dataframe(ref, timeframe)` の resample 結果を `_RESAMPLE_CACHE`（キー (ref, tf)・
値 (mtime, df)・(ref,tf) ごと最新 1 エントリ）でキャッシュする。

回帰観点（先行修正の非回帰を固定）:
  T-1 torn-read 後の resample 復帰（恒久 stale 化しない・最重要）。
  T-2 mtime 連動無効化（CSV 上書きで resample 結果も新内容反映）。
  T-3 有界性（(ref,tf) ごと最新 1 エントリ・mtime ごと増殖しない）。
  T-4 CSV 削除時ヒット（mtime 取得不能でも直前 resample が返る）。
  T-5 ヒット時透過性＋再 resample 抑止（同一 mtime 2 回目は resample_ohlc を呼ばない）。

実ネット非依存（tmp CSV・monkeypatch）。既存 test_dataset.py のヘルパと同方式。
"""

from __future__ import annotations

import csv as _csv
import os as _os

import pandas as _pd

from adapter.compute import dataset

# 決定論的な tmp CSV（loader が要求する open/high/low/close + date 列）。
_CSV_HEADER = ("date", "open", "high", "low", "close")


def _write_csv(path, rows):
    with open(path, "w", newline="") as fh:
        w = _csv.writer(fh)
        w.writerow(_CSV_HEADER)
        w.writerows(rows)


def _register_tmp_ref(monkeypatch, ref, path):
    # ホワイトリストへ tmp ref を一時登録し、base / resample 両キャッシュをクリアして
    #   前テストの残留を断つ。
    monkeypatch.setitem(dataset.DATASET_WHITELIST, ref, path)
    dataset._BASE_CACHE.clear()
    dataset._RESAMPLE_CACHE.clear()


def _advance_mtime(path):
    # mtime を確実に進める（os.utime で決定論化）。
    st = _os.stat(path)
    _os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000_000))


# 1 週間（W-FRI）に複数の日足を含む決定論データ（2020-01-06=Mon .. 2020-01-10=Fri）。
_WEEK1 = [
    ("2020-01-06", 10.0, 12.0, 9.0, 11.0),
    ("2020-01-07", 11.0, 15.0, 10.0, 14.0),
    ("2020-01-08", 14.0, 16.0, 13.0, 15.0),
]


# --------------------------------------------------------------------------- #
# T-1: torn-read 後の resample 復帰（最重要・恒久 stale 化しない）
# --------------------------------------------------------------------------- #
def test_resample_cache_recovers_after_torn_read_then_reflects_on_loader_recovery(
    tmp_path, monkeypatch
):
    # Arrange: 週足を一度取得して resample をキャッシュ。
    csv_path = tmp_path / "torn_w.csv"
    _write_csv(csv_path, _WEEK1)
    _register_tmp_ref(monkeypatch, "_tmp_torn_w", csv_path)
    good = dataset.load_dataframe("_tmp_torn_w", "1W")
    assert len(good) >= 1

    # Act 1: CSV を新内容で上書きし mtime を進める（＝writer がアトミック書込を完了した後の
    #   mtime）。だが「次の base 読込」を torn-read で失敗させる（writer が追記途中の瞬間を
    #   再現）。base は旧 df を返し _BASE_CACHE の mtime を据え置く。
    _write_csv(csv_path, _WEEK1 + [("2020-01-09", 15.0, 30.0, 14.0, 29.0)])
    _advance_mtime(csv_path)

    class _RaisingLoader:
        def load_ohlc_csv(self, *a, **k):
            raise _pd.errors.ParserError("torn last line")

    monkeypatch.setattr(dataset, "_load_loader", lambda: _RaisingLoader())
    served = dataset.load_dataframe("_tmp_torn_w", "1W")

    # Assert 1: base は旧 df へフォールバック → 旧 base の resample が返る（恒久 stale でない）。
    assert served.equals(good)

    # Act 2: loader が復帰する（mtime は Act 1 で進めた値のまま・writer は既に書き終えている）。
    #   ここで再度 mtime を進めないのが要点：torn-read を起こした mtime と同一世代で base が
    #   正常読込へ復帰する。base は新世代 mtime を焼く。
    #   ★P-1 違反（_csv_mtime 独立呼び）だと、Act 1 の torn-read 時点で resample キャッシュが
    #   「進行済 mtime → 旧 base の resample」で汚染されており、復帰後も同一 mtime でヒットし
    #   続けて新内容が反映されない（恒久 stale）→ このアサートが Red になる。
    #   ★_baked_mtime（正）だと torn-read 中 resample キャッシュは旧 mtime のままで、復帰時に
    #   base が焼く新世代 mtime と不一致 → MISS → 新内容で再 resample。
    monkeypatch.undo()
    monkeypatch.setitem(dataset.DATASET_WHITELIST, "_tmp_torn_w", csv_path)
    recovered = dataset.load_dataframe("_tmp_torn_w", "1W")

    # Assert 2: 新内容（30.0 の high を含む週）が resample に反映される（stale 解消）。
    assert float(recovered["high"].max()) == 30.0


# --------------------------------------------------------------------------- #
# T-2: mtime 連動無効化（CSV 上書きで resample 結果も新内容反映）
# --------------------------------------------------------------------------- #
def test_resample_cache_reflects_new_content_after_csv_mtime_changes(tmp_path, monkeypatch):
    csv_path = tmp_path / "live_w.csv"
    _write_csv(csv_path, _WEEK1)
    _register_tmp_ref(monkeypatch, "_tmp_live_w", csv_path)
    first = dataset.load_dataframe("_tmp_live_w", "1W")

    # Act: 翌週（2020-01-13=Mon）の足を追加し mtime を進める → 週足本数が増える。
    _write_csv(csv_path, _WEEK1 + [("2020-01-13", 20.0, 25.0, 19.0, 24.0)])
    _advance_mtime(csv_path)
    second = dataset.load_dataframe("_tmp_live_w", "1W")

    # Assert: 新しい週足が反映される（resample キャッシュが base mtime に連動して無効化）。
    assert len(second) == len(first) + 1


# --------------------------------------------------------------------------- #
# T-3: 有界性（(ref,tf) ごと最新 1 エントリ・mtime ごと増殖しない）
# --------------------------------------------------------------------------- #
def test_resample_cache_holds_single_entry_per_ref_tf_after_repeated_updates(
    tmp_path, monkeypatch
):
    csv_path = tmp_path / "bounded_w.csv"
    _write_csv(csv_path, _WEEK1)
    _register_tmp_ref(monkeypatch, "_tmp_bounded_w", csv_path)

    # Act: 同一 (ref,"1W") を 3 回 mtime 変化させて取得する。
    for i in range(3):
        _write_csv(csv_path, _WEEK1 + [("2020-01-09", 15.0, 16.0 + i, 14.0, 15.0)])
        _advance_mtime(csv_path)
        dataset.load_dataframe("_tmp_bounded_w", "1W")

    # Assert: (ref,tf) ごとに最新 mtime の 1 エントリのみ保持（mtime ごと増殖しない）。
    assert len(dataset._RESAMPLE_CACHE) == 1
    assert ("_tmp_bounded_w", "1W") in dataset._RESAMPLE_CACHE


# --------------------------------------------------------------------------- #
# T-4: mtime 不変ならヒット（再読込・再 resample を走らせない）
#   ISSUE-278 #5: 以前は「CSV 削除でも返る」ことを期待しており、素材消失時に古い断面を無期限
#   配信する挙動を仕様として固定していた。プローブを mtime 据え置きの内容書換へ替える。
#   削除時のフェイルクローズは marketdata/tests/test_stale_serving_fail_close.py が固定する。
# --------------------------------------------------------------------------- #
def test_resample_cache_serves_cached_when_mtime_unchanged(tmp_path, monkeypatch):
    import os as _os

    csv_path = tmp_path / "cached_w.csv"
    _write_csv(csv_path, _WEEK1)
    _register_tmp_ref(monkeypatch, "_tmp_cached_w", csv_path)
    first = dataset.load_dataframe("_tmp_cached_w", "1W")

    # Act: 内容を書き換え、mtime だけ元へ戻す（再読込が走れば別の週足になる）。
    st = _os.stat(csv_path)
    _write_csv(csv_path, [(d, o + 100.0, h + 100.0, low + 100.0, c + 100.0)
                          for (d, o, h, low, c) in _WEEK1])
    _os.utime(csv_path, ns=(st.st_atime_ns, st.st_mtime_ns))
    second = dataset.load_dataframe("_tmp_cached_w", "1W")

    # Assert: 直前の resample 結果が返る（再 resample せずヒット）。
    assert second.equals(first)


# --------------------------------------------------------------------------- #
# T-5: ヒット時透過性＋再 resample 抑止（同一 mtime 2 回目は resample_ohlc を呼ばない）
# --------------------------------------------------------------------------- #
def test_resample_cache_hit_is_transparent_and_skips_resample(tmp_path, monkeypatch):
    csv_path = tmp_path / "spy_w.csv"
    _write_csv(csv_path, _WEEK1)
    _register_tmp_ref(monkeypatch, "_tmp_spy_w", csv_path)

    calls = {"n": 0}
    _real = dataset.resample_ohlc

    def _spy(df, rule):
        calls["n"] += 1
        return _real(df, rule)

    monkeypatch.setattr(dataset, "resample_ohlc", _spy)

    # Act: 同一 mtime で 2 回取得。
    first = dataset.load_dataframe("_tmp_spy_w", "1W")
    second = dataset.load_dataframe("_tmp_spy_w", "1W")

    # Assert: 値等価（透過性）かつ resample は 1 回だけ（2 回目はヒットで再 resample しない）。
    assert second.equals(first)
    assert calls["n"] == 1
