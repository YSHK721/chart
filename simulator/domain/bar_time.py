"""`Bar.time` の時刻表現 → epoch 秒の正規化（domain 層・単一ソース）。

A-3（取得窓を全 `MarketDataPort` 実装へ効かせる）で新設。従来この正規化は
`simulator/main/tester_settings/window.py` にのみ存在したが、窓デコレータ
（`simulator/adapter/repository/windowed_market_data.py`）も同じ比較を要するため、
書き直せば手書き複製になる。正規化の対象は `Bar.time` の型契約そのものであり、
その所有者は domain 層である。よって実体を本モジュールへ置き、`window.py` と
窓デコレータの双方が**同一オブジェクト**を読む（複製 0）。

受理集合（= `Bar.time` の型契約）の定義は本モジュールの ``EPOCH_CONVERTERS`` が唯一持つ。
`simulator/domain/bar.py` は表現を列挙し直さず `is_supported_time` を呼んで構築時に表明する
（ISSUE-411 スライス 3）。

依存規律（`bar.py` と同じ）: 標準ライブラリ・domain 例外・`datawindow`（標準ライブラリ
のみで構成される中立共有パッケージ）に依存する。numpy / pandas は直接にも transitively
にも import しない（``numpy.datetime64`` は duck typing で判定する。``import
simulator.domain.bar_time`` 後に ``numpy`` が ``sys.modules`` へ載らないことを実測で
確認済み）。

実測に基づく確定事項（推測しない）:
    B-1: `bar.time` の実体は経路で異なる。comma 形式 CSV ローダ
         （`adapter/repository/ohlc_csv.py`）は CSV の値をそのまま採用し epoch 整数、
         MT5 タブ形式ローダ（`adapter/repository/ohlc_mt5_csv.py`）は
         ``np.datetime64`` を生成する（両実装の `_extract` 実読）。
    B-2: 窓境界は UTC aware datetime（`main/tester_settings/window.py`
         `resolve_data_window` が `_midnight_utc` で生成する）。
    B-3: naive datetime を `datetime.timestamp()` に掛けるとプロセスのローカル TZ で
         解釈される。naive を UTC とみなすことでこの環境依存という
         **原因そのものを除去**する（症状回避ではない）。
    B-4: その datetime → epoch 変換は**窓境界の正規化と同一の規則**である。実体は中立
         共有パッケージ `datawindow.half_open.epoch_seconds_of_datetime` が唯一所有し、
         本モジュールの `EPOCH_CONVERTERS` と Candle 段（`marketdata/csv_source.py`）が
         同じ関数オブジェクトを読む。分けて書いていた時期は解釈が食い違っていた（実測:
         `TZ=Asia/Tokyo`・naive `datetime(2025, 1, 10)` で 32400 秒差・ISSUE-401 🟡-2）。
         `marketdata` は `simulator` を import できない（依存方向）ため、共有点は両
         パッケージの外側へ置く。

拡張点（OCP）: 時刻表現の追加は **2 つの表へ 1 エントリずつ**である——受理集合と秒変換を
持つ ``EPOCH_CONVERTERS`` と、同じ判定関数を鍵にミリ秒変換を持つ ``_MILLIS_CONVERTERS``
（`epoch_millis` は出力単位だけが違う。§9.0）。既存エントリ・利用側（`epoch_seconds` /
`is_supported_time` / `epoch_millis`）は改変しない。

    **なぜ 1 表に畳まないか**: ``EPOCH_CONVERTERS`` の 3 要素という形を
    `is_supported_time` / `epoch_seconds` / `epoch_millis` と**検定 4 ファイル**が
    展開している（実測 2026-09-11: 参照は検定 6 ファイル、うち 3 要素で展開するのは
    test_bar_time_epoch / test_bar_time_millis / test_window_boundary_single_source /
    test_tick_window_single_source の 4 本）。4 要素へ変えると `epoch_seconds` 側の
    実装に手が入る——§9.0 の要求は「`epoch_seconds` の挙動を変えずに出力単位を増やす」
    ことである。「どの表現を受けるか」は上表ただ 1 つが持ち、下表が持つのは「どの単位で
    出すか」だけなので、受理集合が 2 か所になることはない。

    **なぜ既定で秒×1000 に倒さないか**: 秒未満を持つ表現を足してミリ秒側を忘れたとき、
    既定があると §9.0 が是正した欠陥（秒への潰れ）が**黙って**再発する。取り落ちは
    値で埋めず、下の `_verify_the_two_tables_cover_the_same_set` が読込時に落とす。
"""
from __future__ import annotations

import numbers
from datetime import datetime
from typing import Any, Callable

# B-4: datetime → epoch の実体は中立共有パッケージが唯一所有する（窓境界の正規化と同一
# 規則）。本モジュールは書き直さず、その**関数オブジェクトそのもの**を表へ載せる。
from datawindow.half_open import epoch_seconds_of_datetime
from simulator.domain.exceptions import ConfigError


def is_epoch_integer(value: Any) -> bool:
    """整数（`numpy.int64` を含む）か。``bool`` は時刻ではないため除外する。

    本関数は ``EPOCH_CONVERTERS`` の**整数エントリの判定関数そのもの**である（写しを
    作らない）。公開名で読めるようにしているのは、`Bar.time` が epoch 整数か否かで
    **出力の表現を選ぶ**利用側が存在するためである——`simulator/tools/walk_forward_cli.py`
    `_normalize_span` と `simulator/tools/run_is_oos_cli.py` `normalize_time` は、
    バーの時刻表現に合わせて int 秒 / ``numpy.timedelta64`` ないし
    ``numpy.datetime64`` を返し分ける（`bar.time` との生比較が engine 側に存在する
    ため二重表現そのものは残す）。

    利用側が判定を書き写すと何が起きるか（実測・ISSUE-412 (B)/(D)）:
        手書きの ``isinstance(value, int)`` は ``isinstance(np.int64(1), int)`` が
        **False**（numpy 2.4.6 実測）であるため、comma 形式 CSV 由来の実型
        （``numpy.int64``）を取り落とす。受理集合が本表と利用側の 2 か所で
        食い違い、同一時刻で表現が割れる（例外は出ない）。写しを作らせないために
        判定の実体は本関数 1 つだけとする。
    """
    return isinstance(value, numbers.Integral) and not isinstance(value, bool)


def _from_integer(value: Any) -> int:
    return int(value)


def _is_datetime(value: Any) -> bool:
    return isinstance(value, datetime)


def _is_numpy_datetime64(value: Any) -> bool:
    """``numpy.datetime64``（numpy を import せず duck typing で判定する）。"""
    return hasattr(value, "astype") and type(value).__name__ == "datetime64"


def _from_numpy_datetime64(value: Any) -> int:
    return int(value.astype("datetime64[s]").astype("int64"))


#: 1 秒あたりのミリ秒。**この関係の所有者は本モジュールただ 1 つ**である。
#:
#: 公開するのは、ミリ秒と秒を往復する利用側が外側に実在するためである——
#: `simulator/adapter/trace/columnar_run_trace.py` は `epoch_millis` で得た値を
#: 窓判定（epoch 秒を受ける・§6.4 の JSON 契約）へ落とす。そこで `1000` を書き直すと
#: 「秒とミリ秒の関係」が 2 か所の所有者を持つ（複製は必ず取り残しを生む）。
MILLIS_PER_SECOND = 1000


def _millis_from_integer(value: Any) -> int:
    """epoch **秒**の整数を epoch ミリ秒へ。

    整数エントリの契約は「値は epoch 秒」である（`_from_integer` がそのまま返している
    ことがその宣言）。したがって単位換算は掛け算であり、秒未満は元から存在しない。
    """
    return int(value) * MILLIS_PER_SECOND


def _millis_from_numpy_datetime64(value: Any) -> int:
    """``numpy.datetime64`` を epoch ミリ秒へ（秒未満をミリ秒まで保存する）。

    実ティックの tick_time はこの表現で届く（`adapter/execution/tick_model.py`
    _to_domain_time が `pandas.Timestamp.to_datetime64()` を返す）。store の実 dtype は
    ``datetime64[ms]`` / ``datetime64[us]`` の双方が実在するため、単位は明示して
    ``datetime64[ms]`` へ揃える（ミリ秒より細かい成分は切り捨てる＝丸めない）。
    """
    return int(value.astype("datetime64[ms]").astype("int64"))


def _millis_from_datetime(value: Any) -> int:
    """``datetime`` を epoch ミリ秒へ（naive は UTC・B-3 と同じ規則）。

    秒の部分は共有実体 `epoch_seconds_of_datetime` から採り、ここでは**秒未満だけ**を
    足す。秒の解釈規則（naive=UTC・オフセットの扱い）を書き直すと、同じ datetime が
    単位ごとに違う時刻へ化ける（ISSUE-401 で 32,400 秒差を実測済みの同型）。
    """
    return (
        epoch_seconds_of_datetime(value) * MILLIS_PER_SECOND
        + value.microsecond // 1000
    )


#: 契約タグ: `Bar.time` の受理集合に属するエントリ。
BAR = "BAR"
#: 契約タグ: 窓境界の受理集合に属するエントリ（`Bar.time` ではない）。
WINDOW = "WINDOW"

#: 時刻表現 → epoch 秒の変換器（判定順に評価する。表現の追加＝1 エントリ追加）。
#:
#: 各エントリは (判定, 変換, 契約タグ) の 3 つ組である。本表は**2 つの契約**を載せる:
#:   - ``BAR``   : `Bar.time` の受理集合（epoch int / ``numpy.datetime64``）。
#:                 既存契約「`pd.Timestamp` 禁止」（`domain/trade_record.py` /
#:                 `domain/exceptions.py` / `adapter/execution/tick_model.py` に明文）と一致する。
#:   - ``WINDOW``: 窓境界の受理集合（``datetime``。`main/tester_settings/window.py`
#:                 `resolve_data_window` が aware datetime で生成する）。
#: タグを持たせる理由（ISSUE-411 レビュー 🔴-3）: 分離前は `is_supported_time` が
#: ``datetime`` も受理し、`pd.Timestamp` が `datetime` のサブクラスであるため
#: 「`Bar.time` に `pd.Timestamp` 禁止」の明文より契約が広くなっていた。
#: 判定述語は互いに素である（int / datetime / datetime64 は相互に非包含）ため、
#: タグの導入で `epoch_seconds` の挙動は変わらない。
EPOCH_CONVERTERS: (
    "tuple[tuple[Callable[[Any], bool], Callable[[Any], int], str], ...]"
) = (
    (is_epoch_integer, _from_integer, BAR),
    (_is_numpy_datetime64, _from_numpy_datetime64, BAR),
    # B-4: 窓境界と同じ関数オブジェクト（複製を持たない）。
    (_is_datetime, epoch_seconds_of_datetime, WINDOW),
)


#: 時刻表現 → epoch **ミリ秒**の変換器（RUN_TRACE_BASIC_DESIGN §9.0）。
#:
#: **鍵は `EPOCH_CONVERTERS` の判定関数オブジェクトそのもの**である。判定（＝受理集合
#: ＝「どの表現を受けるか」）は上表が唯一持ち、本表が持つのは「どの単位で出すか」だけ
#: である。両者は別の関心であり、判定を書き写すと受理集合が 2 箇所で食い違う
#: （`is_epoch_integer` の docstring が実測で示している失敗と同型）。
#:
#: なぜ `EPOCH_CONVERTERS` の各エントリへ 4 つ目の要素を足さないか: 同表を 3 要素で
#: 展開しているのは `is_supported_time` / `epoch_seconds` / `epoch_millis` と検定 4
#: ファイルであり（数え直しは上の module docstring「なぜ 1 表に畳まないか」）、形を
#: 変えると `epoch_seconds` 側の実装に手が入る。§9.0 の要求は「`epoch_seconds` の
#: 挙動を変えず出力単位を増やす」ことなので、既存表には触れない。
#:
#: 取り落とし（表現を足してこちらを忘れる）は下の読込時ゲートと
#: `simulator/tests/unit/test_bar_time_millis.py` の網羅ゲートが機械的に赤にする。
_MILLIS_CONVERTERS: "dict[Callable[[Any], bool], Callable[[Any], int]]" = {
    is_epoch_integer: _millis_from_integer,
    _is_numpy_datetime64: _millis_from_numpy_datetime64,
    _is_datetime: _millis_from_datetime,
}


def _verify_the_two_tables_cover_the_same_set() -> None:
    """2 表の取り落としを**読込時**に落とす（起動時 fail-stop）。

    なぜ読込時か（実測・2026-09-11）: ゲートが無いと、``EPOCH_CONVERTERS`` だけへ
    表現を足した状態で `epoch_seconds` は通り、`epoch_millis` は `_MILLIS_CONVERTERS`
    の添字で **`KeyError`** になる。それは「未対応の時刻表現」（`epoch_millis` が宣言する
    `ConfigError`）ではなく**宣言の食い違い**であり、しかも新しい表現が初めて現れた
    run の途中で落ちる。宣言の不整合は起動時に落とすのが本プロジェクトの規律である
    （先例: `simulator/sim_ui/framework/serve_sim_indicators.py` の
    verify_delegated_surface — 宣言した面が内側に無ければ構築時に落とす）。

    `assert` にしないのは、`-O` で消えて宣言の食い違いが素通りするためである。

    事後条件: 2 表の鍵集合が一致していれば何もしない（現状はこれ。よって
        既存の全入力に対する挙動は 1 bit も変わらない）。
    例外: 片側に無い表現があれば `ConfigError`（不足している判定関数を名指す）。
    """
    missing = [
        matches.__name__
        for matches, _convert, _tag in EPOCH_CONVERTERS
        if matches not in _MILLIS_CONVERTERS
    ]
    if missing:
        raise ConfigError(
            "受理する時刻表現にミリ秒変換がありません: "
            f"{missing}。EPOCH_CONVERTERS へ表現を足したら、同じ判定関数を鍵として "
            "_MILLIS_CONVERTERS へも 1 エントリ足してください"
            "（既定で秒×1000 に倒すと秒への潰れが黙って再発する・§9.0）",
            context={"missing_millis_converters": missing},
        )


_verify_the_two_tables_cover_the_same_set()


def is_supported_time(value: Any) -> bool:
    """``value`` が `Bar.time` の型契約（= ``EPOCH_CONVERTERS`` の受理集合）に属するか。

    受理集合の定義は ``EPOCH_CONVERTERS`` が唯一持つ。本述語はそこから導出するだけで、
    対応表現を列挙し直さない（写しを作れば表への追加に追随せず契約が二重定義になる）。
    `Bar` の構築時契約検査（`simulator/domain/bar.py`）が読む（ISSUE-411）。

    見るのは ``BAR`` タグのエントリだけである。``WINDOW`` タグ（``datetime``）は
    窓境界の表現であって `Bar.time` ではない（`pd.Timestamp` は ``datetime`` の
    サブクラスであり、含めると既存の「`pd.Timestamp` 禁止」契約より広くなる）。
    """
    return any(matches(value) for matches, _, tag in EPOCH_CONVERTERS if tag == BAR)


def epoch_seconds(value: Any) -> int:
    """`bar.time` / 窓境界を epoch 秒（int）へ正規化する。

    事前条件: ``value`` は ``EPOCH_CONVERTERS`` が扱える時刻表現。
    事後条件: UTC 基準の epoch 秒を返す。
    例外: 未対応の表現は ``ConfigError``（推測で解釈しない）。
    """
    # 契約タグは問わない（窓境界の正規化にも使うため全エントリを見る）。
    for matches, convert, _tag in EPOCH_CONVERTERS:
        if matches(value):
            return convert(value)
    raise ConfigError(
        f"epoch 秒へ正規化できない時刻表現です: {type(value).__name__}",
        context={"value_type": type(value).__name__, "value": str(value)},
    )


def epoch_millis(value: Any) -> int:
    """`bar.time` / ティック時刻を epoch **ミリ秒**（int）へ正規化する（§9.0）。

    `epoch_seconds` との関係は**単位だけ**である。受理する時刻表現は同じ
    （``EPOCH_CONVERTERS`` の受理集合を唯一の定義として読む）で、判定の順序も同じ。
    したがって `epoch_millis(v) // 1000 == epoch_seconds(v)` が全受理表現で成り立ち、
    「窓が通した点の time 列が窓の外」（§6.5.0 が禁じる食い違い）が起こらない。

    なぜ秒ではなく本関数が要るか（実測・2026-09-10）:
        実ティック 1 ヶ月 run（JP225 2026-01）の評価点 1,036,394 行のうち
        **407,745 行（39.3%）** が秒精度では他の行と同じ時刻になり、1 つの秒を
        最大 14 行が共有していた。時間軸としての推移が読めない。

    事前条件: ``value`` は ``EPOCH_CONVERTERS`` が扱える時刻表現。
    事後条件: UTC 基準の epoch ミリ秒を返す。ミリ秒より細かい成分は切り捨てる。
    例外: 未対応の表現は ``ConfigError``（`epoch_seconds` と同じ契約・推測で解釈しない）。
        下の添字が `KeyError` になる状態（2 表の食い違い）は
        `_verify_the_two_tables_cover_the_same_set` が読込時に排除しているため、
        本関数から出る例外は ``ConfigError`` だけである。
    """
    # 受理集合と判定順は `EPOCH_CONVERTERS` が唯一持つ（ここで列挙し直さない）。
    for matches, _convert, _tag in EPOCH_CONVERTERS:
        if matches(value):
            return _MILLIS_CONVERTERS[matches](value)
    raise ConfigError(
        f"epoch ミリ秒へ正規化できない時刻表現です: {type(value).__name__}",
        context={"value_type": type(value).__name__, "value": str(value)},
    )
