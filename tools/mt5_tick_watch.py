#!/usr/bin/env python3
"""tools/mt5_tick_watch.py — MT5 増分ティック供給の常駐ループ（Composition Root）。

本ファイルが持ってよいのは**組み立ての順序**と**運用の面**（CLI・周期・バックオフ・ログ）だけ
である。列・木レイアウト・時刻変換・M1 の書式・ロールアップ対象足といった規則は 1 つも
持たない（すべて marketdata/mt5_ticks/ と `marketdata/tick_m1.py` の権威へ委譲する）。
規則を tools に置きたくなったら、それは合成点ではなくライブラリの仕事である
（``tools/__init__.py`` の宣言・``tools/tests/test_tools_composition_declaration.py`` が施行）。

1 周期の順序（設計 §4）とその理由:

    取得 → 検証 → 取込（ジャーナル追記）→ 表示系列（閉じた分の M1・上位足）→
    日次確定（parquet）→ 再構築（権威経路での是正）

    検証はどの書込よりも先である。後に回すと Fail-Stop 時に部分的に書かれた台帳が残り、
    「取れていないのか壊れているのか」が区別できなくなる。日次確定と再構築は**日が閉じた
    ときだけ**で、当日は 1 度も parquet 化しない（1 周期ごとに当日全量を書き直さない）。

再開点（コールドスタート）:
    再開点はジャーナルが正である（受信の一次記録だから）。ジャーナルが無い＝コールドスタート
    のときだけ ``--from`` を要求する。``now-30 分`` のような既定を作らないのは、「どこから
    取り直したか」が運用者に見えないまま欠測が埋まらない状態を避けるためである（E-10）。

``--from`` はサーバラベル（端末の壁時計）である:
    UTC→ラベルの逆変換は多価であり `marketdata/mt5_ticks/server_clock.py` は実装しない。
    よって運用者が渡すのは端末が見せている時刻そのもの（またはその epoch ms）である。

秘密は環境変数 ``MT5_BRIDGE_SECRET`` のみ（引数にもファイルにも置かない）。
"""
from __future__ import annotations

import argparse
import datetime as dt
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, List, NamedTuple, Optional, Sequence, Set, Tuple

# repo 根を sys.path へ（``python3 tools/mt5_tick_watch.py`` の直接起動でも marketdata を
#   import できるようにする。既存 tools と同じ様式・tools/verify_tick_immutability.py 参照）。
_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from marketdata.mt5_ticks import cursor as cursor_rules  # noqa: E402
from marketdata.mt5_ticks import http_source, ingest, rebuild, usecases, wire  # noqa: E402
from marketdata.mt5_ticks.cursor import Cursor  # noqa: E402
from marketdata.mt5_ticks.port import Mt5SupplyError, SupplyUnavailable  # noqa: E402

Row = Tuple[int, float, float]

#: 秘密の唯一の供給元。
SECRET_ENV = "MT5_BRIDGE_SECRET"
#: 運用既定（設計 §4）。
DEFAULT_SYMBOL = "JP225"
DEFAULT_ENDPOINT = "http://172.16.162.129:8771"
DEFAULT_KEY_ID = "mt5-bridge"
DEFAULT_INTERVAL_SECONDS = 5.0
#: これより短い周期で端末を叩かない（V-5 の実測で確かめる下限）。
MIN_INTERVAL_SECONDS = 2.0
DEFAULT_REF = "jp225_mt5"

#: 失敗時の待ち（×2 → 上限 → ブレーカ）。
BACKOFF_CAP_SECONDS = 60.0
BREAKER_AFTER_FAILURES = 8
BREAKER_SECONDS = 600.0

#: 再開点を探すときに遡るジャーナルの日数。これを超える中断は ``--from`` で明示させる
#: （黙って何日でも遡ると、欠測を埋めたのか飛ばしたのかが運用者に見えない）。
RESTORE_LOOKBACK_DAYS = 2

#: トークン解決の探り窓（1 ms・1 行）。応答ヘッダのサーバ名だけが目的である。
_TOKEN_PROBE_ROWS = 1

#: 終了コード。
EXIT_OK = 0
EXIT_USAGE = 2
EXIT_FAIL_STOP = 3


class SystemClock:
    """実時刻（UTC）。日跨ぎ・確定条件の判断に使う唯一の時刻源。"""

    def now(self) -> dt.datetime:
        return dt.datetime.now(dt.timezone.utc)


class WatchSettings(NamedTuple):
    """運用の面（CLI で決まる値）。規則は 1 つも含まない。"""

    symbol: str
    endpoint: str
    key_id: str
    interval: float
    data_dir: Any
    ref: str
    from_label: "Optional[int]"
    publish: bool
    quiet: bool


class WatchState(NamedTuple):
    """周期をまたいで持ち越すもの。"""

    cursor: Cursor
    pending: "List[Row]"
    days: "Set[dt.date]"
    latest_day: "Optional[dt.date]"


# ---------------------------------------------------------------------
# 運用の面
# ---------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    """CLI（秘密は引数に置かない＝環境変数のみ）。"""
    parser = argparse.ArgumentParser(
        description="MT5 の増分ティックを取り込み、表示系列まで供給し続ける（読み取りのみ）。"
    )
    parser.add_argument("--symbol", default=DEFAULT_SYMBOL, help=f"銘柄（既定 {DEFAULT_SYMBOL}）")
    parser.add_argument("--endpoint", default=DEFAULT_ENDPOINT,
                        help=f"VM 側 feed の URL（既定 {DEFAULT_ENDPOINT}）")
    parser.add_argument("--key-id", default=DEFAULT_KEY_ID, help="署名に使う鍵 ID")
    parser.add_argument("--interval", type=float, default=DEFAULT_INTERVAL_SECONDS,
                        help=f"ポーリング周期・秒（既定 {DEFAULT_INTERVAL_SECONDS}"
                             f"・下限 {MIN_INTERVAL_SECONDS}）")
    parser.add_argument("--data-dir", default=None,
                        help="データ基点（既定は marketdata の DATA_DIR）")
    parser.add_argument("--ref", default=DEFAULT_REF, help=f"表示系列の ref（既定 {DEFAULT_REF}）")
    parser.add_argument("--from", dest="from_label", default=None,
                        help="コールドスタートの再開点。サーバラベルの壁時計"
                             "（例 '2026-09-01 12:00:00'）か epoch ミリ秒")
    parser.add_argument("--once", action="store_true", help="1 周期だけ実行して終わる")
    parser.add_argument("--no-publish", action="store_true",
                        help="表示系列（M1・上位足）へ書かない（取り込みだけ回す）")
    parser.add_argument("--quiet", action="store_true", help="周期ごとのログを出さない")
    return parser


def parse_from_label(text: Any) -> int:
    """``--from`` をサーバラベルの epoch ミリ秒へ読む（UTC へ変換しない）。"""
    raw = str(text).strip()
    if raw.lstrip("+-").isdigit():
        return int(raw)
    try:
        when = dt.datetime.fromisoformat(raw)
    except ValueError as exc:
        raise ValueError(
            f"--from が読めません: {raw!r}。'YYYY-MM-DD HH:MM:SS'（サーバの壁時計）"
            " か epoch ミリ秒で指定してください。"
        ) from exc
    if when.tzinfo is not None:
        raise ValueError(
            f"--from にタイムゾーンを付けないでください: {raw!r}。"
            " ここで受け取るのは端末が見せているサーバ時刻そのものです。"
        )
    return int(when.replace(tzinfo=dt.timezone.utc).timestamp() * 1000)


def next_delay(failures: int, *, interval: float) -> float:
    """次の周期までの待ち（設計 §4: ×2 → 上限 60 秒 → 連続 8 回で 600 秒）。

    上限を置くのは、待ちが伸び続けると復旧しても気付くのが遅れるからである。ブレーカは
    その逆で、明らかに直っていない相手を 60 秒ごとに叩き続けないためにある。
    """
    if failures <= 0:
        return float(interval)
    if failures >= BREAKER_AFTER_FAILURES:
        return BREAKER_SECONDS
    return min(float(interval) * (2 ** failures), BACKOFF_CAP_SECONDS)


def resolve_token(source: Any, *, symbol: str, at_msc: int) -> str:
    """応答ヘッダのサーバ名から保存トークンを決める（**起動時 1 回だけ**）。

    トークンは ``<銘柄>@<サーバ>`` であり、サーバ名は端末しか知らない。よって最初の 1 回だけ
    1 ms・1 行の窓で探りを入れ、以降の周期では発行しない（周期あたりの取得は 1 回のまま）。
    トークンの組み立て規則は :func:`marketdata.mt5_ticks.ingest.token_for` が権威である
    （VM 側はトークンを作らない）。

    **未検証**: 実端末の範囲取得 API が「始点＝終点」の窓に対して空配列を返すか、
    エラーにするかは V-1（端点包含の意味論）と同じ未確定事項である。エラーになる場合でも
    起動時に非 0 で止まり書込は 0 なので、静かに壊れることはない（V-1 の実測後に、必要なら
    この 1 箇所だけを直す）。
    """
    response = source.fetch(
        symbol=symbol, from_msc=int(at_msc), to_msc=int(at_msc), max_rows=_TOKEN_PROBE_ROWS
    )
    return ingest.token_for(symbol, response.server)


# ---------------------------------------------------------------------
# 1 周期の合成
# ---------------------------------------------------------------------

@dataclass
class SupplyCycle:
    """1 周期を組み立てる（各段はユースケースが持ち、ここは順序だけを持つ）。"""

    poll: usecases.PollOnce
    publish: "Optional[usecases.PublishDataset]"
    finalize: usecases.FinalizeDay
    token: str
    ref: str
    data_dir: Any

    def __call__(self, state: WatchState) -> "Tuple[WatchState, usecases.PollResult]":
        result = self.poll(state.cursor)

        days = set(state.days) | set(result.days)
        latest = max([d for d in (state.latest_day, *result.days) if d is not None], default=None)

        # 持ち越しは「形成中の分を次の周期の畳みに混ぜる」ためだけに在る。畳まない運用
        #   （--no-publish）で溜めると、受信したティックが 1 つ残らずメモリに積み上がる。
        #   出力に使わないものを持ち続けるのは、作ってから捨てる計算と同じ欠陥である。
        pending: "List[Row]" = []
        if self.publish is not None:
            published = self.publish(list(state.pending) + list(result.new_rows))
            pending = list(published.pending_rows)

        # 日が閉じたものだけを確定し、そのうえで権威経路で是正する（設計 §10 の裁定）。
        settled = self.finalize(days=days, latest_observed_day=latest)
        if settled:
            rebuild.rebuild_days(
                settled.keys(), symbol=self.token, ref=self.ref, data_dir=self.data_dir,
                update_rollups=self.publish is not None,
            )
            days -= set(settled)

        return WatchState(result.cursor, pending, days, latest), result


def build_cycle(settings: WatchSettings, *, source: Any, token: str, clock: Any) -> SupplyCycle:
    """設定と供給元から 1 周期を組み立てる（依存の向きはここで 1 回だけ決まる）。"""
    return SupplyCycle(
        poll=usecases.PollOnce(
            source=source, symbol=settings.symbol, token=token, data_dir=settings.data_dir
        ),
        publish=(
            usecases.PublishDataset(ref=settings.ref, data_dir=settings.data_dir, clock=clock)
            if settings.publish else None
        ),
        finalize=usecases.FinalizeDay(
            token=token, data_dir=settings.data_dir, clock=clock
        ),
        token=token,
        ref=settings.ref,
        data_dir=settings.data_dir,
    )


def lookback_days(clock: Any) -> "List[dt.date]":
    """起動時に見にいく日（**再開点の復元と未確定日の種付けで同じ窓を使う**）。

    2 つが別の窓を持つと、片方だけが遡れる日ができる（再開はできるのに確定は落ちる、
    あるいはその逆）。窓は 1 箇所で決める。
    """
    today = clock.now().date()
    return [today - dt.timedelta(days=i) for i in range(RESTORE_LOOKBACK_DAYS)]


def restore_or_start(
    settings: WatchSettings, *, token: str, days: "Sequence[dt.date]"
) -> "Optional[Cursor]":
    """再開点を決める。ジャーナル優先・無ければ ``--from``・どちらも無ければ ``None``。

    ジャーナルが在るときは ``--from`` より優先する（受信の一次記録が正であり、指定で巻き戻すと
    カーソルの単調性が壊れる）。指定が無視されたことは黙らずに知らせる。
    """
    restored = usecases.RestoreCursor(token=token, data_dir=settings.data_dir)(days=days)
    if restored is not None:
        if settings.from_label is not None:
            _stderr(
                f"--from は無視します（ジャーナルから再開: cursor={restored.cursor_ms}）。"
                " 再開点はジャーナルが正です。"
            )
        return restored
    if settings.from_label is None:
        return None
    return cursor_rules.Cursor(cursor_ms=settings.from_label, boundary_rows=())


# ---------------------------------------------------------------------
# 常駐
# ---------------------------------------------------------------------

def run(
    settings: WatchSettings,
    *,
    source: Any,
    clock: Any = None,
    sleep: "Callable[[float], Any]" = time.sleep,
    cycles: "Optional[int]" = None,
    log: "Callable[[str], Any]" = None,
) -> int:
    """周期を回す。``cycles`` は**成功した**周期の数で数える（失敗は消化しない）。"""
    clock = SystemClock() if clock is None else clock
    say = log if log is not None else (lambda line: None if settings.quiet else _stderr(line))

    probe_at = settings.from_label if settings.from_label is not None else 0
    try:
        token = resolve_token(source, symbol=settings.symbol, at_msc=probe_at)
    except SupplyUnavailable as exc:
        _stderr(f"供給元へ到達できません: {exc}")
        return EXIT_FAIL_STOP
    except (Mt5SupplyError, wire.WireError) as exc:
        _stderr(f"供給元の応答が契約を満たしません: {exc}")
        return EXIT_FAIL_STOP

    window = lookback_days(clock)
    start = restore_or_start(settings, token=token, days=window)
    if start is None:
        _stderr(
            "コールドスタートには --from が要ります（再開点を推測しません）。"
            " 端末の壁時計で開始点を指定してください。"
        )
        return EXIT_USAGE

    # 前回の停止で確定に至らなかった日を引き継ぐ。当プロセスが観測した日だけを候補にすると、
    #   日 D の途中で止まって D+1 に再起動したとき D の確定が二度と呼ばれない。
    seeded = usecases.UnfinalizedDays(token=token, data_dir=settings.data_dir)(days=window)
    if seeded:
        _stderr(f"未確定の日を引き継ぎます: {[str(d) for d in seeded]}")

    cycle = build_cycle(settings, source=source, token=token, clock=clock)
    state = WatchState(cursor=start, pending=[], days=set(seeded), latest_day=None)
    done = 0
    failures = 0

    while cycles is None or done < cycles:
        try:
            state, result = cycle(state)
        except SupplyUnavailable as exc:
            failures += 1
            # 周期の予算がある実行（``--once`` など）は、ブレーカが開く連続失敗回数で打ち切る。
            #   成功した周期しか数えないため、待てば直る障害が続くと 1 周期も消化されないまま
            #   永久に回り続ける（--help の「1 周期だけ実行して終わる」と食い違う）。
            #   常駐（予算なし）は変えない — そちらは直るまで待ち続けることが仕事である。
            if cycles is not None and failures >= BREAKER_AFTER_FAILURES:
                _stderr(
                    f"供給が {failures} 回続けて失敗したため打ち切ります（周期の予算あり）: {exc}"
                )
                return EXIT_FAIL_STOP
            delay = next_delay(failures, interval=settings.interval)
            _stderr(f"供給が一時的に失敗しました（{failures} 回目・{delay} 秒待ちます）: {exc}")
            sleep(delay)
            continue
        except (Mt5SupplyError, wire.WireError, cursor_rules.CursorContractError) as exc:
            # カーソル規約の破れも「待っても直らない」側である。型集合から漏れると、
            #   常駐はトレースバックを吐いて exit 1 で落ち、運用者には未知のクラッシュに見える。
            #   `cursor.py` は依存ゼロを保つため、繋ぐのは合成点であるここの責務。
            _stderr(f"供給の前提が崩れました（再試行しません）: {exc}")
            return EXIT_FAIL_STOP

        failures = 0
        done += 1
        say(
            f"received={result.received} appended={result.appended}"
            f" dropped={result.dropped} cursor={state.cursor.cursor_ms}"
        )
        if cycles is not None and done >= cycles:
            break
        sleep(settings.interval)
    return EXIT_OK


def _stderr(line: str) -> None:
    sys.stderr.write(line + "\n")


def settings_from(args: argparse.Namespace) -> WatchSettings:
    """引数を運用設定へ（既定の解決はここ 1 箇所）。"""
    from marketdata.paths import DATA_DIR

    return WatchSettings(
        symbol=args.symbol,
        endpoint=args.endpoint,
        key_id=args.key_id,
        interval=float(args.interval),
        data_dir=DATA_DIR if args.data_dir is None else args.data_dir,
        ref=args.ref,
        from_label=None if args.from_label is None else parse_from_label(args.from_label),
        publish=not args.no_publish,
        quiet=bool(args.quiet),
    )


def main(
    argv: "Optional[Sequence[str]]" = None,
    *,
    source: Any = None,
    clock: Any = None,
    sleep: "Callable[[float], Any]" = time.sleep,
) -> int:
    """CLI 入口。``source`` / ``clock`` / ``sleep`` は検定と埋め込みのための差し替え口である。"""
    args = build_parser().parse_args(argv)

    if args.interval < MIN_INTERVAL_SECONDS:
        _stderr(
            f"--interval は {MIN_INTERVAL_SECONDS} 秒以上にしてください（受け取った値:"
            f" {args.interval}）。端末を叩き過ぎない。"
        )
        return EXIT_USAGE

    secret = os.environ.get(SECRET_ENV, "")
    if not secret:
        _stderr(f"環境変数 {SECRET_ENV} が未設定です（秘密は環境変数からのみ受け取ります）。")
        return EXIT_USAGE

    try:
        settings = settings_from(args)
    except ValueError as exc:
        _stderr(str(exc))
        return EXIT_USAGE

    if source is None:
        source = http_source.HttpTickSource(
            settings.endpoint, key_id=settings.key_id, secret=secret.encode("utf-8")
        )

    try:
        return run(
            settings, source=source, clock=clock, sleep=sleep,
            cycles=1 if args.once else None,
        )
    except KeyboardInterrupt:
        _stderr("停止しました。")
        return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
