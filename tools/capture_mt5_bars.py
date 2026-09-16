#!/usr/bin/env python3
"""MT5 端末から JP225 の足を取得し、端末エクスポートと同じ書式の CSV に書く（読み取りのみ）。

ISSUE-511 前提 (b)（上位足の spread 集約規則を実測するための素材）。依頼者が Windows VM
（OANDA-Japan MT5 Live にログイン済みの端末）で実行する。検定は
``tools/tests/test_capture_mt5_bars.py``（MetaTrader5 不在のコンテナで fake を注入）。

## 実行（Windows VM・MT5 端末が起動している状態で）

    python capture_mt5_bars.py --out-root <クローン>/simulator/tests/fixtures

``--out-root`` が唯一の引数（必須）。出力は
``<out-root>/mt5_bars/<期間ID>/JP225_<足>_<期間ID>.csv``（21 本）と
``<out-root>/mt5_bars/manifest.json``。``--out-root`` を必須にしてリポジトリの位置を
推測しないのは、``tools/capture_mt5_symbol_spec.py`` の find_repo_root 事故の教訓である。
本ファイルは MetaTrader5 以外に stdlib しか使わない（VM へは本ファイル 1 本だけを持ち込めばよい）。

渡すのは **VM に置いたクローンの ``simulator/tests/fixtures``** である（最終の置き場が
``simulator/tests/fixtures/mt5_bars/`` であり、そこへ直接書けば
``simulator/tests/fixtures/mt5_bars/.gitattributes`` が改行変換を止める場所に落ちるため）。
別の場所へ書いてから移すと、移す経路で CRLF が変換されても manifest の sha256 が合わなく
なるだけで、原因が端末側にあるようにしか見えない。

## 終了コードと Fail-Stop

21 組すべてを書けたときだけ終了コード 0 で終わる。次の前提が崩れたときは終了コード 2 とし、
理由を ``[FAIL-STOP] ...`` の 1 行として標準エラーへ出す:

- MetaTrader5 パッケージが無い、または ``initialize()`` が失敗した
- ``account_info()`` が None、または接続先サーバが :data:`EXPECTED_SERVER` でない
- ``symbol_select()`` が失敗した、または ``symbol_info()`` が None
- 足が None・0 本・列が欠ける・要求窓の外・時刻が狭義単調増加でない（窓の外は捨てずに止める）
- 書き出し先に同名のファイルが既にある（上書きは一切しない）

これ以外の OS エラーは捕まえないので traceback のまま落ちる（実測: ``--out-root`` に指定した
場所の親がファイルだと NotADirectoryError）。``[FAIL-STOP]`` が出ていない異常終了はこれである。

取得の段で止まったときは 1 バイトも書かない（書き始めるのは 21 組を取り終えてからである）。
書き出しの段で既存ファイルに当たったときは、それより前に書いた CSV は残り manifest は作られない
（既存ファイルへの上書きは常に拒否する。取り直すときは、前回書いた ``mt5_bars/`` の出力を
片付けてから実行する）。

## 持ち帰り（J-3: VM のクローンから専用ブランチへ git push）

持ち帰るのは終了コード 0 で終わった実行の出力である（途中で止まった出力には manifest が無い）。

    git add simulator/tests/fixtures/mt5_bars/<期間ID>/JP225_<足>_<期間ID>.csv
    git add simulator/tests/fixtures/mt5_bars/manifest.json
    git commit -m "..."
    git push origin <専用ブランチ>

パスは上のように明示する（``git add -A`` は使わない。VM のクローンに落ちている環境依存物を
無差別に拾うため）。運搬でバイト列が変わっていないことは manifest の sha256 で確かめる。

## 書式（参照実装＝端末の既存エクスポート）

simulator/tests/fixtures/mt5/ma_slope_jp225_202501/input/ の既存エクスポート 2 本と byte 一致する:
タブ区切り・見出し 1 行・全行 CRLF・BOM なし・日付 ``2025.01.02``・時刻 ``01:00:00``・
価格は ``symbol_info().digits`` 桁（JP225 は 1 桁）・``<VOL>`` は real_volume（既存エクスポートは
全行 0 のため列の対応は推論）。日付と時刻は足のラベル（サーバ時刻）の壁時計そのままで、
タイムゾーン変換も夏時間の補正もしない。

## 時刻

- 戻り値の time 列はサーバ時刻のラベルを UTC とみなした epoch 秒（ISSUE-511 の T5 突合で確定）。
- 引数の datetime の時計は ``copy_rates_range`` については**未確定**。本スクリプトは
  ``tools/mt5_tick_feed.py`` の V-1 実測（ティック取得）と同じ規則（ローカル naive の往復）で
  渡す（``terminal_datetime``）。同じ規則が足にも効くかは推論なので、要求窓の外の足が
  1 本でも返れば**捨てずに Fail-Stop** する（捨てると時計のずれが黙って隠れる）。

## 安全性（接続先は実弾のライブ口座である）

使う端末 API は読み取り系だけで、:data:`ALLOWED_TERMINAL_APIS` に挙げたものに限る。
発注系（``order_*``）は参照しない。これらは宣言ではなく ``tools/tests/test_capture_mt5_bars.py``
の AST 走査が施行する。施行する項目は :data:`ENFORCED_CHECKS` が宣言するものに限り（この散文
ではなくその宣言が出所で、検定側の登録簿との一致を検定が固定する）、どれも本ファイルの構文
だけを見る:

- 名前 ``mt5`` への属性参照が :data:`ALLOWED_TERMINAL_APIS` に収まること
- ``getattr(mt5, ...)`` の名前が定数で（組み立てない）、:data:`ALLOWED_TERMINAL_APIS` と
  :data:`ALLOWED_PACKAGE_ATTRS` の**和**に収まること（両方に属することではない）
- **``getattr`` の第 2 引数を組み立てないこと（受け手の名前に依らない）**。上の 2 つは受け手が
  名前 ``mt5`` のときしか見ないので、端末を別名の仮引数へ渡した先で
  ``getattr(terminal, "order_" + n)`` と名前を組み立てる形は、実行されない分岐（``except`` 節
  など）に置くと検定をすべて緑のまま通る（実測）。ただしこの項目が閉じるのは
  **``getattr`` へ組み立てた名前を渡す形だけ**である（定数名で引く形は下の 2 つが閉じる）
- 走査が名前 ``mt5`` に依存するので、**代入の右辺と関数の既定引数に端末そのものを置かないこと**
  （``terminal = mt5``・``a, b = mt5, 1``・``{"t": mt5}``・``def g(term=mt5)``）。端末を実引数
  として渡した先までは追わない（追う側は必ず漏れるので、施行できる範囲だけを名乗る）
- ``order_`` で始まる属性参照が 1 つも無いこと
- **``getattr`` が定数で発注系の名前（``order_`` で始まる名前）を引かないこと（受け手に依らない）**。
  ``getattr(terminal, "order_send")({})`` は第 2 引数が定数なので 1 つ上の項目に掛からず、
  属性参照ではないのでこの 1 つ前の項目にも掛からない（実測: この項目以外のどの走査も無検出）
- **``getattr`` 以外の動的属性アクセスを使わないこと（受け手に依らない）**。
  ``terminal.__getattribute__("order_send")({})``・``terminal.__dict__["order_send"]({})``・
  ``operator.attrgetter("order_send")(terminal)({})``・``vars(terminal)["order_send"]({})``
  は、どれも別名レシーバ＋定数名なので先行するどの項目も無検出である（実測。``operator`` は
  stdlib なので import の走査も緑のまま）。本体はこれらの動的属性アクセスを 1 つも使わないので、
  出現 0 件を誤検出なしで施行できる。同じ語を定数文字列で綴った getattr(terminal, "__dict__")
  の形もこの項目が閉じる（語の集合は検定側の 1 箇所が唯一の出所。ここへ語数と綴りを書き写すと
  2 通り目の定義になり、増やしても何も落ちないので書かない）。走査が見るのは識別子
  （属性名・変数名）と getattr の定数第 2 引数だけなので、上の例が語を綴っても掛からない

施行の範囲はここまでである。見ないのは importlib 経由の解決・exec/eval へ渡す文字列・
``getattr`` を素の名前で呼ばない形（``builtins.getattr(t, n)``・``getattr(*args)``）で、
どれも実測で :data:`ENFORCED_CHECKS` のどの走査にも掛からない（import の走査には
``importlib``・``builtins`` が現れるが、どちらも stdlib なので緑のまま）。
属性名を組み立てる形そのものは見ないのではなく、
:data:`ENFORCED_CHECKS` の ``no_composed_getattr_name`` が閉じる（実測）。
本ファイルは VM で人が手で実行する 1 ファイルの道具であり、この走査は多重防御の 1 枚で
あって唯一の防御ではない。

サーバが OANDA-Japan MT5 Live でなければ足を 1 本も取らずに止まる。これは構文には現れないので、
同じ検定ファイルの挙動側（別サーバを名乗る fake で ``copy_rates_range`` の呼出が 0 回）が施行する。
接続は 1 実行につき 1 回で、取得を終えて接続を閉じてから書く。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, NamedTuple, Sequence

#: 本スクリプトの識別子（manifest の出所）。
GENERATOR = "tools/capture_mt5_bars.py"

SYMBOL = "JP225"

#: 取得を許すサーバ（これ以外なら端末から足を取らない）。
EXPECTED_SERVER = "OANDA-Japan MT5 Live"

#: 触ってよい端末 API（接続先は実弾のライブ口座・読み取りのみ）。発注系はここに無い。
#: 検定 ``tools/tests/test_capture_mt5_bars.py`` が、本体の ``mt5.*`` がこの集合に収まること
#: （走査）と、この集合そのもの（リテラル固定）の両方を施行する＝広げる変更は検定に必ず現れる。
ALLOWED_TERMINAL_APIS = frozenset({
    "initialize", "shutdown", "last_error", "account_info", "terminal_info",
    "symbol_select", "symbol_info", "copy_rates_range",
    "TIMEFRAME_M1", "TIMEFRAME_M5", "TIMEFRAME_M15", "TIMEFRAME_M30",
    "TIMEFRAME_H1", "TIMEFRAME_H4", "TIMEFRAME_D1", "TIMEFRAME_W1", "TIMEFRAME_MN1",
})

#: 端末 API ではないが読んでよいモジュール属性（manifest に載せるパッケージ版）。
#: 検定 ``tools/tests/test_capture_mt5_bars.py`` が、本体の ``getattr(mt5, ...)`` の定数名が
#: ``ALLOWED_TERMINAL_APIS`` との和に収まること（走査）と、この集合そのもの（リテラル固定）の
#: 両方を施行する＝広げる変更は検定に必ず現れる。
ALLOWED_PACKAGE_ATTRS = frozenset({"__version__"})

#: 上の「安全性」節が施行を名乗る項目の識別子（宣言はここが唯一の出所）。
#: 散文で「施行は次の N つ」と数えると、実際に施行している走査とずれても何も落ちない
#: （このブランチで同種の誇大が 6 件）。検定 ``tools/tests/test_capture_mt5_bars.py`` の
#: 登録簿 _ENFORCED_SCANNERS の鍵とこの集合の一致と、どの走査にも検出力の実測がある
#: ことを固定するので、片側だけを増減させる変更は必ず検定に現れる。
ENFORCED_CHECKS = (
    "terminal_attrs_within_the_allowlist",
    "getattr_on_the_terminal_is_literal_and_allowed",
    "no_composed_getattr_name",
    "no_terminal_bound_to_another_name",
    "no_order_attribute_reference",
    "no_order_named_getattr",
    "no_dynamic_attribute_access",
)

#: ``--out-root`` の下に作るディレクトリと manifest の名前。
OUTPUT_DIR = "mt5_bars"
MANIFEST_NAME = "manifest.json"

#: 端末エクスポートの見出しと改行（参照実装と byte 一致）。
HEADER = "<DATE>\t<TIME>\t<OPEN>\t<HIGH>\t<LOW>\t<CLOSE>\t<TICKVOL>\t<VOL>\t<SPREAD>"
NEWLINE = "\r\n"

#: ``copy_rates_range`` の戻り値が持つべき列名。
#: 一次情報: https://www.mql5.com/en/docs/python_metatrader5/mt5copyratesrange_py
#: "Returns bars as the numpy array with the named time, open, high, low, close,
#: tick_volume, spread and real_volume columns."
RATE_COLUMNS = ("time", "open", "high", "low", "close", "tick_volume", "spread", "real_volume")

#: 足のラベル境界（要求窓の始端を切り下げる格子）。秒で表せる足はその周期。
#: H4 はサーバ日 00:00 起点（86400 は 14400 の倍数なので epoch の 4 時間格子と一致する）。
GRID_SECONDS = {
    "M1": 60, "M5": 300, "M15": 900, "M30": 1800, "H1": 3600, "H4": 14400, "D1": 86400,
}

#: W1 / MN1 のラベルの起点。週は日曜（``datetime.weekday()`` の 6）、月は 1 日。
#:
#: 一次情報（https://www.mql5.com/en/docs/python_metatrader5/mt5copyratesrange_py および
#: enum_timeframes）は **週の起点の曜日を述べていない**（"1 week" とのみ）。切り下げれば
#: どちらでも先頭の足を覆える: 同ドキュメントの "Bars with the open time >= date_from are
#: returned" より、日曜 00:00 から要求すれば、週が日曜起点ならその足、月曜起点なら翌日の
#: 月曜ラベルの足が、いずれも窓に入る。MN1 を 1 日へ切り下げるのも同じ理由で安全である。
#: 切り下げないと、始端がラベルの途中に当たる組で先頭の足が黙って欠ける
#: （期間の始端の曜日の実測: 2024-12-29=日曜・2026-03-23=月曜・2020-05-01=金曜）。
#: 実際の起点の曜日は、持ち帰った W1 のエクスポートで観測する。
WEEK_LABEL_WEEKDAY = 6
MONTH_LABEL_DAY = 1


class CaptureError(RuntimeError):
    """取得の前提が崩れたことを表す（Fail-Stop）。黙って書かない。"""


# ---------------------------------------------------------------------
# 実行計画（MT5 を知らない）
# ---------------------------------------------------------------------

class Job(NamedTuple):
    """1 組の取得。窓は半開区間 [from_s, to_s)（サーバ時刻ラベルの epoch 秒）。"""

    period_id: str
    timeframe: str
    from_s: int
    to_s: int


def _label_epoch(y: int, m: int, d: int) -> int:
    return int(datetime(y, m, d, tzinfo=timezone.utc).timestamp())


def _period(start: "tuple[int, int, int]", end: "tuple[int, int, int]",
            timeframes: Sequence[str]) -> "list[Job]":
    from_s, to_s = _label_epoch(*start), _label_epoch(*end)
    period_id = f"{datetime(*start):%Y%m%d}_{datetime(*end):%Y%m%d}"
    return [Job(period_id, tf, from_s, to_s) for tf in timeframes]


def build_plan(jobs: Iterable[Job]) -> "tuple[Job, ...]":
    """計画を固定する。組 (期間ID, 足) の重複は拒む（同じ取得を 2 回しない）。"""
    plan = tuple(jobs)
    seen: "set[tuple[str, str]]" = set()
    for job in plan:
        key = (job.period_id, job.timeframe)
        if key in seen:
            raise CaptureError(f"計画に組 {key} が重複しています")
        seen.add(key)
    return plan


_ALL_TIMEFRAMES = ("M1", "M5", "M15", "M30", "H1", "H4", "D1", "W1", "MN1")
_LONG_TIMEFRAMES = ("D1", "W1", "MN1")

#: ISSUE-511 前提 (b) の 21 組（J-2: 期間の終端の日を含む＝to は翌日 0 時の半開区間）。
PLAN: "tuple[Job, ...]" = build_plan([
    *_period((2024, 12, 29), (2025, 2, 1), _ALL_TIMEFRAMES),
    *_period((2026, 3, 23), (2026, 5, 1), _ALL_TIMEFRAMES),
    *_period((2020, 5, 1), (2026, 9, 1), _LONG_TIMEFRAMES),
])


# ---------------------------------------------------------------------
# 時刻と書式（MT5 を知らない）
# ---------------------------------------------------------------------

def terminal_datetime(label_s: int) -> datetime:
    """端末へ渡す naive datetime（``tools/mt5_tick_feed.py`` の V-1 と同じ規則）。

    MetaTrader5 パッケージは naive をローカル時刻として epoch 化する（V-1 実測）。
    ``fromtimestamp`` で同じローカル規則の壁時計へ写すので、端末に届く epoch は
    ``label_s`` に一致する（ローカル tz によらない）。
    """
    return datetime.fromtimestamp(int(label_s))


def label_wallclock(label_s: int) -> datetime:
    """ラベル epoch 秒の壁時計（サーバ時刻そのまま・tz 変換なし）。"""
    return datetime.fromtimestamp(int(label_s), timezone.utc).replace(tzinfo=None)


def aligned_from(timeframe: str, label_s: int) -> int:
    """要求窓の始端を、その時間足の足ラベル境界へ切り下げる（純関数）。

    サーバラベルの壁時計で計算し、タイムゾーン変換はしない。規則は ``GRID_SECONDS``・
    ``WEEK_LABEL_WEEKDAY``・``MONTH_LABEL_DAY``（根拠はそれぞれの docstring）。
    """
    step = GRID_SECONDS.get(timeframe)
    if step is not None:
        return int(label_s) - int(label_s) % step
    wall = label_wallclock(label_s)
    if timeframe == "MN1":
        return _label_epoch(wall.year, wall.month, MONTH_LABEL_DAY)
    if timeframe == "W1":
        day = _label_epoch(wall.year, wall.month, wall.day)
        return day - ((wall.weekday() - WEEK_LABEL_WEEKDAY) % 7) * 86400
    raise CaptureError(f"時間足 {timeframe!r} のラベル境界の規則がありません")


def request_window(job: Job) -> "tuple[int, int]":
    """端末へ要求する窓 [切り下げた始端, 期間の終端)（終端はそのまま）。"""
    return aligned_from(job.timeframe, job.from_s), job.to_s


def _label_text(label_s: int) -> str:
    return f"{label_wallclock(label_s):%Y-%m-%d %H:%M:%S}"


def format_rows(rates: Any, digits: int) -> bytes:
    """足の列を端末エクスポートと同じバイト列にする（見出し＋全行 CRLF・BOM なし）。"""
    lines = [HEADER]
    for r in rates:
        wall = label_wallclock(r["time"])
        prices = "\t".join(f"{float(r[k]):.{digits}f}" for k in ("open", "high", "low", "close"))
        lines.append(
            f"{wall:%Y.%m.%d}\t{wall:%H:%M:%S}\t{prices}"
            f"\t{int(r['tick_volume'])}\t{int(r['real_volume'])}\t{int(r['spread'])}"
        )
    return (NEWLINE.join(lines) + NEWLINE).encode("ascii")


# ---------------------------------------------------------------------
# 出力（MT5 を知らない）
# ---------------------------------------------------------------------

def relative_path(job: Job) -> str:
    """``<期間ID>/JP225_<足>_<期間ID>.csv``（manifest にもこの形で記録する）。"""
    return f"{job.period_id}/{SYMBOL}_{job.timeframe}_{job.period_id}.csv"


def write_new(path: Path, data: bytes) -> None:
    """新規にだけ書く（``'xb'``: 既存ファイルがあれば FileExistsError・改行変換を受けない）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "xb") as fp:
        fp.write(data)


class Session(NamedTuple):
    """接続中に 1 回だけ読む端末と銘柄の属性。"""

    server: str
    terminal_build: Any
    package_version: "str | None"
    digits: int


def _iso_utc(moment: datetime) -> str:
    aware = moment.replace(tzinfo=timezone.utc) if moment.tzinfo is None else moment
    return aware.astimezone(timezone.utc).replace(tzinfo=None).isoformat(timespec="seconds") + "Z"


def file_entry(job: Job, rates: Any, data: bytes) -> "dict[str, Any]":
    request_from_s, request_to_s = request_window(job)
    return {
        "path": relative_path(job),
        "period_id": job.period_id,
        "timeframe": job.timeframe,
        "from_label": _label_text(job.from_s),
        "to_label_exclusive": _label_text(job.to_s),
        "request_from_label": _label_text(request_from_s),
        "request_to_label_exclusive": _label_text(request_to_s),
        "rows": len(rates),
        "first_label": _label_text(rates[0]["time"]),
        "last_label": _label_text(rates[-1]["time"]),
        "sha256": hashlib.sha256(data).hexdigest(),
    }


def manifest(session: Session, captured_at: datetime,
             entries: "list[dict[str, Any]]") -> bytes:
    payload = {
        "generator": GENERATOR,
        "symbol": SYMBOL,
        "server": session.server,
        "terminal_build": session.terminal_build,
        "mt5_package_version": session.package_version,
        "digits": session.digits,
        "captured_at_utc": _iso_utc(captured_at),
        "files": entries,
    }
    return (json.dumps(payload, sort_keys=True, indent=2, ensure_ascii=False) + "\n").encode("utf-8")


def write_outputs(out_dir: Path, session: Session, fetched: "list[tuple[Job, Any]]",
                  captured_at: datetime) -> None:
    entries = []
    for job, rates in fetched:
        data = format_rows(rates, session.digits)
        write_new(out_dir / relative_path(job), data)
        entries.append(file_entry(job, rates, data))
    write_new(out_dir / MANIFEST_NAME, manifest(session, captured_at, entries))


# ---------------------------------------------------------------------
# MT5 との境界（注入された mt5 モジュール相当だけを触る）
# ---------------------------------------------------------------------

def _default_mt5():
    """既定の供給元。トップレベル import しない（コンテナで import と --help を通すため）。"""
    import MetaTrader5  # noqa: PLC0415  (遅延 import は意図的)

    return MetaTrader5


def _last_error(mt5: Any) -> str:
    try:
        return f"last_error={mt5.last_error()!r}"
    except Exception as exc:  # 供給元が last_error を持たない場合も原因を落とさない
        return f"last_error=<取得できません: {exc!r}>"


@contextmanager
def mt5_session(mt5: Any):
    """端末との接続を 1 回だけ開いて閉じる。"""
    if not mt5.initialize():
        raise CaptureError(f"mt5.initialize() が失敗しました（{_last_error(mt5)}）")
    try:
        yield mt5
    finally:
        mt5.shutdown()


def timeframe_codes(mt5: Any) -> "dict[str, Any]":
    """時間足名 → 端末の定数（明示の属性参照だけで引く）。"""
    return {
        "M1": mt5.TIMEFRAME_M1, "M5": mt5.TIMEFRAME_M5, "M15": mt5.TIMEFRAME_M15,
        "M30": mt5.TIMEFRAME_M30, "H1": mt5.TIMEFRAME_H1, "H4": mt5.TIMEFRAME_H4,
        "D1": mt5.TIMEFRAME_D1, "W1": mt5.TIMEFRAME_W1, "MN1": mt5.TIMEFRAME_MN1,
    }


def read_session(mt5: Any) -> Session:
    """サーバを確かめ、銘柄の桁を読む。サーバが違えば足を取る前に止まる。"""
    account = mt5.account_info()
    if account is None:
        raise CaptureError(f"mt5.account_info() が None を返しました（{_last_error(mt5)}）")
    if account.server != EXPECTED_SERVER:
        raise CaptureError(
            f"接続先サーバが {account.server!r} です（{EXPECTED_SERVER!r} のみ取得します）"
        )
    if not mt5.symbol_select(SYMBOL, True):
        raise CaptureError(f"mt5.symbol_select({SYMBOL!r}) が失敗しました（{_last_error(mt5)}）")
    info = mt5.symbol_info(SYMBOL)
    if info is None:
        raise CaptureError(f"mt5.symbol_info({SYMBOL!r}) が None を返しました（{_last_error(mt5)}）")
    terminal = mt5.terminal_info()
    version = getattr(mt5, "__version__", None)
    return Session(
        server=account.server,
        terminal_build=None if terminal is None else terminal.build,
        package_version=None if version is None else str(version),
        digits=int(info.digits),
    )


def read_rates(mt5: Any, timeframe_code: Any, job: Job) -> Any:
    """1 組を 1 回で取る。窓の外・0 本・None・非単調は捨てずに Fail-Stop。"""
    from_s, to_s = request_window(job)
    rates = mt5.copy_rates_range(
        SYMBOL, timeframe_code, terminal_datetime(from_s), terminal_datetime(to_s - 1)
    )
    where = f"{job.period_id} {job.timeframe}"
    if rates is None:
        raise CaptureError(f"{where}: copy_rates_range が None を返しました（{_last_error(mt5)}）")
    if len(rates) == 0:
        raise CaptureError(f"{where}: 足が 0 本でした（{_last_error(mt5)}）")
    names = set(getattr(getattr(rates, "dtype", None), "names", None) or rates[0].keys())
    missing = [c for c in RATE_COLUMNS if c not in names]
    if missing:
        raise CaptureError(f"{where}: 戻り値に列 {missing} がありません（列 {sorted(names)}）")
    times = [int(r["time"]) for r in rates]
    outside = [t for t in times if not from_s <= t < to_s]
    if outside:
        raise CaptureError(
            f"{where}: 要求窓 [{_label_text(from_s)}, {_label_text(to_s)}) の外の足が"
            f" {len(outside)} 本あります（最初 {_label_text(outside[0])}）。"
            " 端末へ渡す時刻の解釈が想定と違う可能性があります"
        )
    if any(b <= a for a, b in zip(times, times[1:])):
        raise CaptureError(f"{where}: 足の時刻が狭義単調増加ではありません")
    return rates


def capture(mt5: Any, plan: "Sequence[Job]") -> "tuple[Session, list[tuple[Job, Any]]]":
    """接続 1 回で計画の全組を取る（組ごとに 1 回ずつ）。書くのは接続を閉じた後。"""
    with mt5_session(mt5):
        session = read_session(mt5)
        codes = timeframe_codes(mt5)
        fetched = [(job, read_rates(mt5, codes[job.timeframe], job)) for job in plan]
    return session, fetched


# ---------------------------------------------------------------------
# CLI（Composition Root）
# ---------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=GENERATOR,
        description="MT5 端末から JP225 の足を取得し CSV に書く（読み取りのみ・発注系 API は呼ばない）。",
    )
    parser.add_argument(
        "--out-root", required=True,
        help=f"出力先（この下に {OUTPUT_DIR}/ を作る。既存ファイルは上書きしない）",
    )
    return parser


def main(
    argv: "Sequence[str] | None" = None,
    *,
    mt5: Any = None,
    now: "datetime | None" = None,
) -> int:
    args = build_parser().parse_args(argv)
    captured_at = now or datetime.now(timezone.utc)
    try:
        # 端末は常に名前 mt5 で持つ。別名を作ると、触ってよい端末 API の集合を
        # ``mt5.*`` の走査で施行している検定の外側に抜け道ができる。
        if mt5 is None:
            mt5 = _default_mt5()
        session, fetched = capture(mt5, PLAN)
        out_dir = Path(args.out_root) / OUTPUT_DIR
        write_outputs(out_dir, session, fetched, captured_at)
        print(f"written: {out_dir}（{len(fetched)} ファイル + {MANIFEST_NAME}）")
        return 0
    except (CaptureError, FileExistsError) as exc:
        print(f"[FAIL-STOP] {exc}", file=sys.stderr)
        return 2
    except ModuleNotFoundError as exc:
        print(
            f"[FAIL-STOP] MetaTrader5 パッケージが見つかりません: {exc}。"
            " 本スクリプトは MT5 端末が動く Windows 上で実行してください。",
            file=sys.stderr,
        )
        return 2


if __name__ == "__main__":  # pragma: no cover - CLI エントリ
    sys.exit(main())
