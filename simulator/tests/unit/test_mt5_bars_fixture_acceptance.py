"""VM から届いた MT5 上位足エクスポートの受け入れ検定（ISSUE-511 前提 (b) 段階 2）。

素材は依頼者が Windows VM の MT5 端末で `tools/capture_mt5_bars.py` を実行して取得し、
VM のクローンから git push で持ち帰ったバイト列そのものである（CSV 21 本 + manifest）。
本ファイルが固定するのは「届いた素材が壊れていないこと」と「その素材の出所」であり、
上位足の spread の集約規則そのものは test_mt5_higher_timeframe_spread_is_min.py が別に固定する。

なぜバイト列の検査が要るか: 運搬経路は Windows の git であり、改行変換が起きると
CSV の中身は「読めるまま」壊れる。壊れたことは状態検証（値を読む検定）では気づけず、
原因が端末側にあるようにしか見えない。改行変換を止める設定は
simulator/tests/fixtures/mt5_bars/.gitattributes にあり、その設定が有効であること
（git の属性）は tools/tests/test_capture_mt5_bars.py が別途固定する。本ファイルが見る
のはチェックアウト後の**実バイト列**であり、主張が違う（設定の有無ではなく結果）。

なぜ出所の表明が要るか: sha256 が保証するのは運搬の完全性だけであり、「届いたのがどの
銘柄・どのサーバの足か」については何も言わない。別銘柄・別サーバで同じ手順を踏んだ素材へ
丸ごと差し替えても、バイト列の検査は全部そろって緑のままである（実測）。出所は素材自身の
manifest にしか書かれていないので、そこを読んで表明する検定が要る。

素材が 1 つでも欠けていれば検定は skip ではなく**失敗**する。不在で黙って検査をやめる
検定は、素材が消えたことを緑で隠す（ISSUE-517 と同型）。

素材の所在・ファイル名の規則・行の復号・ラベルの読み方（`csv_path` / `rows_of` /
`parse_label`）は本ファイルが所有する。規則の検定
（test_mt5_higher_timeframe_spread_is_min.py）は同じ規則を書き直さず、ここから import する。
同じ規則を 2 か所に書くと、片方だけ直したときに他方が別の素材を見たまま緑になる。
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path

import pytest

from tools.capture_mt5_bars import EXPECTED_SERVER, SYMBOL

#: 素材の置き場（このファイルからの相対で解決する。cwd に依存しない）。
FIXTURE_ROOT = Path(__file__).resolve().parents[1] / "fixtures" / "mt5_bars"

#: 既存エクスポート（端末が書いた実物＝書式と時計の参照実装）。
EXISTING_EXPORT = (
    Path(__file__).resolve().parents[1]
    / "fixtures" / "mt5" / "ma_slope_jp225_202501" / "input"
    / "JP225_M1_202412230100_202501302359.csv"
)

#: 端末エクスポートの見出し 1 行（タブ区切り・9 列）。
HEADER = "<DATE>\t<TIME>\t<OPEN>\t<HIGH>\t<LOW>\t<CLOSE>\t<TICKVOL>\t<VOL>\t<SPREAD>"

#: 端末が書く <DATE> + <TIME> の書式（ラベルを読む唯一の書式）。
LABEL_FORMAT = "%Y.%m.%d %H:%M:%S"

CRLF = "\r\n"
BOM = b"\xef\xbb\xbf"

PERIOD_A = "20241229_20250201"
PERIOD_B = "20260323_20260501"
PERIOD_LONG = "20200501_20260901"


def manifest() -> dict:
    """manifest を読む（不在なら FileNotFoundError で落ちる＝skip しない）。"""
    return json.loads((FIXTURE_ROOT / "manifest.json").read_bytes().decode("utf-8"))


def entries() -> "list[dict]":
    return manifest()["files"]


def entry_of(period_id: str, timeframe: str) -> dict:
    """1 組の manifest 記載を返す。組が無ければ KeyError で落ちる（skip しない）。"""
    by_pair = {(e["period_id"], e["timeframe"]): e for e in entries()}
    return by_pair[(period_id, timeframe)]


def csv_bytes(relative_path: str) -> bytes:
    return (FIXTURE_ROOT / relative_path).read_bytes()


def csv_path(period_id: str, timeframe: str) -> Path:
    """1 本の CSV の置き場（ファイル名の規則を組み立てる唯一の場所）。"""
    return FIXTURE_ROOT / period_id / f"JP225_{timeframe}_{period_id}.csv"


def data_lines(raw: bytes) -> "list[str]":
    """見出しを除いたデータ行（末尾の空要素は落とす）。"""
    text = raw.decode("ascii")
    return text.split(CRLF)[1:-1]


def rows_of(period_id: str, timeframe: str) -> "list[str]":
    """1 本の CSV のデータ行（不在なら FileNotFoundError で落ちる＝skip しない）。"""
    return data_lines(csv_path(period_id, timeframe).read_bytes())


def header_line(raw: bytes) -> str:
    return raw.decode("ascii").split(CRLF)[0]


def bare_lf_count(raw: bytes) -> int:
    """CRLF を除いた後に残る裸の LF の数（0 なら全行 CRLF）。"""
    return raw.replace(b"\r\n", b"").count(b"\n")


def parse_label(date_field: str, time_field: str) -> datetime:
    """<DATE> と <TIME> の 2 列を 1 つの時刻にする（ラベルを読む唯一の経路）。

    書式が崩れた素材はここで ValueError になって落ちる。ラベルを文字列のまま持ち回ると、
    崩れた書式は「固定長だから辞書順がそのまま時刻順」という前提だけを静かに壊し、
    落ちないまま別の足を比べさせる。
    """
    return datetime.strptime(f"{date_field} {time_field}", LABEL_FORMAT)


def labels_of(period_id: str, timeframe: str) -> "list[datetime]":
    """CSV のラベル列（サーバ時刻の壁時計そのまま）。"""
    return [parse_label(*row.split("\t")[:2]) for row in rows_of(period_id, timeframe)]


def keyed_rows(raw: bytes) -> "dict[datetime, tuple[str, ...]]":
    """ラベル → 残り 7 列。同じラベルの行を突き合わせるための索引。

    鍵を日時にするのは、呼ぶ側が重なりの端を min / max と大小比較で決めるためである
    （文字列の辞書順に依存しない）。
    """
    out = {}
    for line in data_lines(raw):
        fields = line.split("\t")
        out[parse_label(fields[0], fields[1])] = tuple(fields[2:])
    return out


def parse_manifest_label(text: str) -> datetime:
    return datetime.strptime(text, "%Y-%m-%d %H:%M:%S")


# =====================================================================
# 0. 出所（どの銘柄・どのサーバの足か）
# =====================================================================

def test_the_manifest_records_the_symbol_and_server_the_generator_pins():
    """manifest の symbol / server が、生成器が取得を許す 1 組と一致する。

    期待値は tools/capture_mt5_bars.py の定数そのものを読む。生成器は接続先が
    EXPECTED_SERVER でなければ取得を拒み、SYMBOL 以外の銘柄は要求しない。同じ文字列を
    ここへ書き写すと「どの素材が正当か」の台帳が 2 つになり、生成器だけを直したときに
    検定が古い出所を指したまま緑になる。

    実測（manifest の symbol を別銘柄に、server を別サーバに変えた複製）: 落ちるのは本検定
    だけで、本ファイルと規則検定の残りはすべて緑のまま通る。本検定を外すと、その複製に対して
    1 件も落ちない。

    実測（生成器の定数だけを別銘柄・別サーバへ変え、素材は本物のまま）: 本検定が落ちる。
    期待値を同じ文字列の書き写しへ変えた複製では、同じ食い違いが 1 件も落ちずに通った。
    """
    recorded = manifest()

    assert recorded["symbol"] == SYMBOL
    assert recorded["server"] == EXPECTED_SERVER


# =====================================================================
# 1-3. バイト列・行数・書式
# =====================================================================

def test_the_manifest_lists_every_csv_that_is_checked_in_and_nothing_else():
    """素材は 21 本ちょうどで、manifest の一覧とディスク上の一覧が一致する。

    どちらか片方にしか無いファイルは「取り込み漏れ」か「取り残し」であり、以降の
    検定がその 1 本を黙って見ないまま緑になる。
    """
    listed = sorted(e["path"] for e in entries())
    on_disk = sorted(p.relative_to(FIXTURE_ROOT).as_posix() for p in FIXTURE_ROOT.rglob("*.csv"))

    assert listed == on_disk
    assert len(listed) == 21


def test_every_file_matches_the_sha256_the_manifest_recorded():
    """21/21 のバイト列が manifest の sha256 と一致する（運搬中の改変が無い）。"""
    mismatched = [
        e["path"] for e in entries()
        if hashlib.sha256(csv_bytes(e["path"])).hexdigest() != e["sha256"]
    ]

    assert mismatched == []
    assert len(entries()) == 21


def test_every_file_has_the_row_count_the_manifest_recorded():
    """データ行数が manifest の rows と一致する（末尾の切れ落ちが無い）。"""
    wrong = [
        (e["path"], len(data_lines(csv_bytes(e["path"]))), e["rows"])
        for e in entries()
        if len(data_lines(csv_bytes(e["path"]))) != e["rows"]
    ]

    assert wrong == []


def test_every_csv_is_crlf_without_a_bom_and_carries_the_terminal_header():
    """BOM が無い・データ行はすべて CRLF・見出しが端末エクスポートと同一。

    3 つは別々の壊れ方に対応する: BOM は先頭行の日付を汚し、LF への潰れは改行変換が
    効いた証拠であり、見出しの違いは列の意味の取り違えである。

    BOM だけは他より先に表明する。後続の検査は ASCII で復号するので、BOM のあるファイル
    では復号が先に例外を投げ、どの表明にも到達しないまま「復号に失敗した」とだけ見える
    （実測: UnicodeDecodeError）。落ちること自体は変わらないが、原因が読めなくなる。
    """
    with_bom = [e["path"] for e in entries() if csv_bytes(e["path"]).startswith(BOM)]

    assert with_bom == []

    with_bare_lf = [e["path"] for e in entries() if bare_lf_count(csv_bytes(e["path"])) != 0]
    wrong_header = [e["path"] for e in entries() if header_line(csv_bytes(e["path"])) != HEADER]
    not_crlf_terminated = [e["path"] for e in entries()
                           if not csv_bytes(e["path"]).endswith(CRLF.encode("ascii"))]

    assert with_bare_lf == []
    assert wrong_header == []
    assert not_crlf_terminated == []


def test_the_manifest_itself_is_lf_without_a_bom():
    """manifest だけは LF である（CSV と改行が違うこと自体が属性の効いた証拠）。"""
    raw = (FIXTURE_ROOT / "manifest.json").read_bytes()

    assert raw.startswith(BOM) is False
    assert raw.count(b"\r\n") == 0
    assert raw.count(b"\n") > 0


# =====================================================================
# 4. 既存エクスポートとの重なり（引数の時計が正しいことの実証）
# =====================================================================

def test_period_a_m1_agrees_with_the_existing_export_on_every_overlapping_minute():
    """期間 A の M1 が既存エクスポートと重なり区間で 1 行も食い違わない。

    これは書式の一致にとどまらず、端末へ渡した窓の時計が正しいことの実証でもある。
    時計がずれていれば、同じラベルに別の足の値が入るか、重なり区間に穴が空く
    （どちらも「読める CSV」のままなので、値を見ない検定では落ちない）。

    重なりの端は両ファイルが共に持つ最初と最後のラベルで、その内側での欠落を
    両方向に見る（片方向だけだと、一方が他方の部分集合のとき穴を見逃す）。
    """
    new_rows = keyed_rows(csv_bytes(f"{PERIOD_A}/JP225_M1_{PERIOD_A}.csv"))
    old_rows = keyed_rows(EXISTING_EXPORT.read_bytes())
    shared = set(new_rows) & set(old_rows)
    low, high = min(shared), max(shared)
    disagreeing = [k for k in sorted(shared) if new_rows[k] != old_rows[k]]
    missing_in_new = [k for k in sorted(old_rows) if low <= k <= high and k not in new_rows]
    missing_in_old = [k for k in sorted(new_rows) if low <= k <= high and k not in old_rows]

    assert len(shared) == 30807
    assert disagreeing == []
    assert missing_in_new == []
    assert missing_in_old == []
    assert header_line(csv_bytes(f"{PERIOD_A}/JP225_M1_{PERIOD_A}.csv")) == header_line(
        EXISTING_EXPORT.read_bytes())


# =====================================================================
# 5. M1 の日付集合 == D1 のラベル集合
# =====================================================================

@pytest.mark.parametrize(("period_id", "expected_days"), [(PERIOD_A, 24), (PERIOD_B, 29)])
def test_the_m1_dates_are_exactly_the_days_the_d1_file_labels(period_id: str, expected_days: int):
    """M1 に現れる日付の集合と D1 のラベルの日付集合が両方向で一致する。

    片方向の包含だけでは、D1 に余分な足がある場合（取引の無い日を足す）と、M1 に
    D1 の無い日がある場合（日足の取りこぼし）を区別できない。
    """
    m1_days = {label.date() for label in labels_of(period_id, "M1")}
    d1_days = {label.date() for label in labels_of(period_id, "D1")}

    assert sorted(m1_days - d1_days) == []
    assert sorted(d1_days - m1_days) == []
    assert len(d1_days) == expected_days


# =====================================================================
# 6. 切り下げが効いた証拠
# =====================================================================

@pytest.mark.parametrize(("period_id", "timeframe", "expected_first"), [
    (PERIOD_A, "MN1", "2024.12.01"),
    (PERIOD_LONG, "W1", "2020.04.26"),
])
def test_the_first_label_precedes_the_requested_start_because_it_was_rounded_down(
        period_id: str, timeframe: str, expected_first: str):
    """先頭の足のラベルが要求期間の始端より前にある（＝始端を切り下げた証拠）。

    端末は始端以降に開く足しか返さないので、切り下げが無ければこの 1 本は黙って
    欠けていた（欠けても残りの足は正しいままなので、値を見る検定では落ちない）。
    manifest の from_label（切り下げ前）と request_from_label（切り下げ後）の差が
    そのまま観測できる。
    """
    recorded = entry_of(period_id, timeframe)
    first_label = labels_of(period_id, timeframe)[0]

    assert f"{first_label:%Y.%m.%d}" == expected_first
    assert first_label < parse_manifest_label(recorded["from_label"])
    assert first_label == parse_manifest_label(recorded["request_from_label"])


# =====================================================================
# 7. ラベルの規約
# =====================================================================

def test_every_weekly_label_is_a_sunday():
    """W1 のラベルは 332/332 が日曜（公式ドキュメントに記載の無い週の起点の実測）。"""
    labels = labels_of(PERIOD_LONG, "W1")
    not_sunday = [label for label in labels if label.weekday() != 6]

    assert not_sunday == []
    assert len(labels) == 332


def test_every_monthly_label_is_the_first_of_the_month():
    labels = labels_of(PERIOD_LONG, "MN1")
    not_first = [label for label in labels if label.day != 1]

    assert not_first == []
    assert len(labels) == 76


def test_no_daily_label_falls_on_a_weekend():
    """D1 に土日のラベルが無い（週末に足を作らない端末の規約）。"""
    labels = labels_of(PERIOD_LONG, "D1")
    weekend = [label for label in labels if label.weekday() >= 5]

    assert weekend == []
    assert len(labels) == 1638
