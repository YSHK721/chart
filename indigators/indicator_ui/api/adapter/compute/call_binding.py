"""CALL_BINDING（内部設計書 §3.3.3・基本設計 §5.5.4.1）— 指標ごとの呼出規約。

compute_id(+variant) → {callable, output_kind, keyword_params} を保持し、``invoke`` で
既存 add_* を一意・決定論的に呼ぶ。add_btlm のみ fitter を第3位置引数で渡し、他は
df 以降キーワード専用（§5.5.4.1）。fitter enum 文字列 → Fitter 実体化
（ols→OlsBtlmFitter() / tgp→TgpBtlmFitter()）をここで行う。

3 指標はいずれも top-level パッケージ名 ``src`` を使うため、``import src`` では同名衝突し
1 つしか読めない。本モジュールは各指標 src を **ファイルパスから一意なパッケージ名で
読み込む**（既存 src は read-only・改変しない）。描画ライブラリは import しない。

指標を 1 件追加する手順（ISSUE-180・back 側）:
    1. ``_TABLE`` へ ``(compute_id, variant)`` のエントリを 1 件足す。呼出規約（loader /
       output_kind / kind）に加え、必要なら thread_affinity / time_required / latest_meta /
       preprocess を、そして param 既定値 ``params_defaults`` を **同一エントリ内に** 宣言する。
       variant を複数持つ指標は先頭 variant にのみ ``params_defaults`` を書く。
    2. back 側の改変はこれで完了する。``catalog_schema.PARAM_DEFAULTS``（``GET /catalog`` の
       配信値）・``requires_time`` ・``requires_dedicated_worker`` ・``latest_meta`` はいずれも
       本エントリからの導出であり、追加登録は不要（宣言漏れは
       ``indicator_param_defaults`` が ValueError で、テストが構造検査で検出する）。
    3. 既定値を追加・変更したときは front 同期契約 ``api/tests/golden/catalog_defaults.json``
       を更新する（back 配信値 == front 静的フォールバック値のオラクル）。
    4. front（``web/js/usecase/catalog.js`` の IndicatorDef、足内更新対象なら
       ``intrabar_forming_ids.js``）は別アクターの所有物であり、本テーブルからは導出されない
       （``GET /catalog`` は param 既定値のみを配信する契約のため）。front 側の宣言は別途必要。
"""

from __future__ import annotations

import copy
import importlib
import inspect
import sys
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any, Callable, NotRequired, TypedDict

from adapter.compute.module_loader import load_package

# 共有プリミティブ層（``common.applied_price`` 等）を指標 src から絶対 import 可能にする。
#   指標 src（例 moving_averages/src/lwc_chart.py）が ``from common.applied_price import ...``
#   を解決できるよう、ワークスペース根（このファイル: api/adapter/compute/ → parents[5]）を
#   sys.path に追加する（ロード境界で一括設定し、各 src に sys.path ハックを散らさない）。
# ISSUE-087 🟡-3: repo 根/MP api の解決は venv の .pth（tools/install_dev_paths.py）が担う（実行時 sys.path 改変を撤去）。
# ISSUE-174: 兄弟パッケージ層（``moving_averages`` / ``mql_builtins`` / ``profit_system``）の解決点は
#   本ロード境界（_ensure_indigators_on_path）に一本化した。各 src の ``sys.path.insert`` は撤去済み。


def _accepted_kwargs(callable_: Callable, params: dict[str, Any]) -> dict[str, Any]:
    """``callable_`` が実際に受け取るキーワード引数のみへ ``params`` を絞り込む。

    フロントは catalog の全 params（variant 横断）を送るため、当該 variant の add_* が
    取らない引数（例: global へ robust 専用 ``normalize``、robust へ ``require_full``）が
    混入し ``TypeError`` になる。シグネチャに ``**kwargs`` があれば素通し、無ければ
    宣言済みパラメータ名に一致するキーだけを残す（未知キーは黙って捨てる）。
    """
    sig = inspect.signature(callable_)
    if any(p.kind == p.VAR_KEYWORD for p in sig.parameters.values()):
        return dict(params)
    allowed = {
        name for name, p in sig.parameters.items()
        if p.kind in (p.KEYWORD_ONLY, p.POSITIONAL_OR_KEYWORD)
    }
    return {k: v for k, v in params.items() if k in allowed}


# price_range_power の interval（絶対価格刻み）バンド爆発を防ぐ上限/目標。
# 指数等の高価格帯で catalog 既定 0.1 のままだとバンド数（価格レンジ/interval）が
# 数十万に達し計算が事実上停止する。catalog/src の選択肢（core.py INTERVAL_CHOICES）は
# 変更せず（パリティ契約を保つ）、バンド数が上限超過の場合のみ価格規模へ自動適応する。
_PRP_MAX_BANDS = 20000
_PRP_TARGET_BANDS = 3000


def _nice_step(value: float) -> float:
    """1/2/5×10^n の見やすい刻みへ丸める（最低 0.1）。"""
    import math

    if value <= 0:
        return 0.1
    exp = math.floor(math.log10(value))
    base = value / (10 ** exp)
    nice = 1.0 if base <= 1 else 2.0 if base <= 2 else 5.0 if base <= 5 else 10.0
    return max(round(nice * (10 ** exp), 4), 0.1)


def _adapt_prp_interval(df: Any, kw: dict[str, Any]) -> float:
    """price_range_power の interval を価格規模へ適応させる（爆発時のみ粗刻み化）。

    バンド数 = 価格レンジ / interval が ``_PRP_MAX_BANDS`` を超える場合のみ、目標バンド数に
    収まる見やすい刻みへ置換する。低価格帯（sample 等）では元の interval をそのまま保つ。
    レンジは add_price_range_power と同じく range_from/range_to 優先・無指定時は df の low/high。
    """
    interval = kw.get("interval")
    if interval is None or interval <= 0:
        return interval  # 0/None は core 側の検証へ委ねる（挙動を変えない）。
    cols = {str(c).lower(): c for c in df.columns}
    if "low" not in cols or "high" not in cols:
        return interval
    rf, rt = kw.get("range_from"), kw.get("range_to")
    lo = float(rf) if rf is not None else float(df[cols["low"]].min())
    hi = float(rt) if rt is not None else float(df[cols["high"]].max())
    rng = hi - lo
    if rng > 0 and rng / interval > _PRP_MAX_BANDS:
        return _nice_step(rng / _PRP_TARGET_BANDS)
    return interval


def _prp_preprocess(df: Any, kw: dict[str, Any]) -> dict[str, Any]:
    """price_range_power 専用 invoke 前処理フック（ISSUE-097 🟡-7 / ISSUE-098 🟡-6・LSP）。

    従来 ``invoke`` 内に直書きされていた ``if compute_id == "price_range_power" and
    "interval" in kw`` の指標名判定を _BindingSpec の宣言的フックへ昇格し、invoke から
    compute_id 直判定を排除する。挙動は従来と同一（interval があるときのみバンド爆発を
    防ぐ自動適応を行い、無いときは触らない）。
    """
    if "interval" in kw:
        # 高価格帯でのバンド爆発（ハング）を防ぐ自動適応（catalog/src は不変）。
        kw["interval"] = _adapt_prp_interval(df, kw)
    return kw


# --- Latest 増分計算メタ（archetype/min_window/trailing_k）の宣言（ISSUE-097 🟡-6・OCP）---
# latest_meta.py の per-indicator if 連鎖を撤去し、各指標の archetype 分類を _BindingSpec の
# ``latest_meta`` フィールド（params → (archetype, min_window, trailing_k) の resolver）へ
# 一元宣言する。未宣言（field 不在）の指標は latest_meta.py 側の安全既定
# recurrence/full/K=1 へ落ちる（従来不変）。LatestMeta 型はここで import しない
# （latest_meta.py が本 resolver の戻り tuple から構築＝call_binding との循環を回避）。

# ISSUE-233: moving_averages は 4 種すべて「保持した状態を 1 点進める」増分計算
#   （archetype="incremental"・状態器 "moving_averages"）で計算する。full 再計算を行わない
#   ため所要は窓長に依らず一定になる。値は full と bit 一致する（sma/ema/smma は
#   ``*_on_buffer`` の prev_calculated 契約、lwma は走行和を授受する
#   ``linear_weighted_ma_on_buffer_stateful`` が full の漸化をそのまま継続するため）。
#
#   min_window は None（full）のままにする。増分器が扱えないパラメータ（平滑化あり等）で
#   落ちる従来経路は、tail による短縮を行わない厳密一致設計を維持する必要があるため
#   （sma/lwma は core がスライド和の再帰であり、tail で開始点を変えると末尾値に浮動小数
#   ドリフト ~1e-15 が乗る）。この理由で従来 sma/lwma を "window" と分類していた。


def _moving_averages_latest_meta(
    params: dict[str, Any],
) -> tuple[str, int | None, int | None, str | None]:
    del params  # 4 種・全パラメータで同一宣言（適用可否の判定は増分器 prepare が持つ）。
    return ("incremental", None, 1, "moving_averages")


def _price_range_power_latest_meta(
    params: dict[str, Any],
) -> tuple[str, int | None, int | None]:
    # 価格軸分布（非時系列）。末尾K切りしない（全件・trailing_k=None）。
    return ("axis_distribution", None, None)


# tickvol は本体（点ごとの写像）と外れ値水準（因果ローリング＋イベント蓄積）の複合である。
#   水準はバー t までに**確定したイベント観測**すべてに依存し、必要な履歴長は上限を持たない
#   （イベント頻度はデータ依存。実測 5m で 1 件 / 35.7 バー＝直近 50 件に 1,800 バー必要）。
#   よって有限 tail は取れず、full 再計算では足内更新のたびに全窓を走り直すことになる。
#   ISSUE-233 と同じ真因なので同じ解を採る＝「保持した状態を 1 点進める」増分計算を宣言する。
#   増分器が扱えないパラメータでは prepare が None を返し従来の full 経路へ落ちる。


def _tickvol_latest_meta(
    params: dict[str, Any],
) -> tuple[str, int | None, int | None, str | None]:
    del params  # 全パラメータで同一宣言（適用可否の判定は増分器 prepare が持つ）。
    return ("incremental", None, 1, "tickvol")


# indigators/ ルート（このファイル: api/adapter/compute/ → parents[4] = indigators/）。
_INDIGATORS = Path(__file__).resolve().parents[4]

# 一意パッケージ名の接頭辞。3 指標が共通 top-level 名 ``src`` を使うため、各指標を
# ``_<indicator>_src`` という衝突しない名前で sys.modules へ登録する（同名 src 回避）。
_SRC_MODULE_PREFIX = "_"
_SRC_MODULE_SUFFIX = "_src"


def _src_module_name(indicator: str) -> str:
    """指標名から一意なパッケージ名（``_<indicator>_src``）を組み立てる。"""
    return f"{_SRC_MODULE_PREFIX}{indicator}{_SRC_MODULE_SUFFIX}"


def _ensure_indigators_on_path() -> None:
    """``indigators/`` を sys.path へ 1 回だけ登録する（ISSUE-174・冪等）。

    指標 src は兄弟パッケージ（``moving_averages`` / ``mql_builtins`` / ``profit_system``）を
    top-level 名で import する。その解決点を **ロード境界であるここ 1 か所**に置き、各 src の
    最内層に散っていた ``sys.path.insert``（13 本）を撤去する。既に登録済みなら何もしない。
    """
    path = str(_INDIGATORS)
    if path not in sys.path:
        sys.path.insert(0, path)


def _load_src_package(indicator: str) -> ModuleType:
    """指標 src パッケージを一意なパッケージ名で読み込む（同名 ``src`` 衝突を回避）。

    importlib 機構は ``module_loader.load_package`` に集約（重複解消・振る舞い不変）。
    一意名 ``_<indicator>_src`` を与え、相対 import（``from .bands import``）と
    sys.modules キャッシュは load_package が担保する。

    exec 前に ``indigators/`` を sys.path へ載せる（src 内の兄弟パッケージ絶対 import の解決点）。
    """
    _ensure_indigators_on_path()
    return load_package(_src_module_name(indicator), _INDIGATORS / indicator / "src")


# tgp::btlm の MCMC 設定（_TGP_SEED / _BTE_PRESETS / _DEFAULT_SAMPLES / _fitter_factory）は
# _TABLE の直後に定義する。_DEFAULT_SAMPLES は指標記述子 _TABLE の tgp_btlm
# ``params_defaults["mcmc_samples"]`` から導出するため、_TABLE の定義後でなければ解決できない
# （ISSUE-180: param 既定値の単一情報源を _TABLE へ統合）。


def _load_callable(indicator: str, attr: str) -> Callable:
    """指標 src の lwc_chart から add_* を取り出す（read-only）。"""
    src = _load_src_package(indicator)
    lwc = importlib.import_module(src.__name__ + ".lwc_chart")
    return getattr(lwc, attr)


def indicator_src(indicator: str) -> ModuleType:
    """指標 src パッケージを一意名で読み込んで返す（read-only・無改変参照）。

    増分器（``adapter.compute.incremental``）が指標 src の **公開関数**（``*_on_buffer`` /
    ``rolling_ols_window_end`` 等）を呼ぶための唯一の入口。ロード機構（同名 ``src`` 衝突の
    回避・sys.path の解決点）を本モジュールへ閉じ込め、増分器側へ importlib を散らさない。
    """
    return _load_src_package(indicator)


def profit_band_empty_bucket_error() -> type:
    """profit_band src の ``EmptyBucketError`` 型を返す（LSP 是正・型識別用）。

    profit_band src を一意パッケージ名で遅延ロードし専用例外型を返す。adapter はこの型で
    ``isinstance`` 判定し、「必須バケット空(empty_series)」と「検証失敗(validation)」の二意味を
    日本語メッセージ片照合でなく型で区別する。ロードは sys.modules キャッシュ済みのため、
    invoke で送出された例外インスタンスの型と同一クラスオブジェクトを返す（isinstance が成立）。
    """
    return _load_src_package("profit_band").EmptyBucketError


# tgp_btlm ソース 8 択化（kind-twirling-hollerith.md §4）。既存 4 択（open/high/low/close）は
# 参照実装 build_btlm_bands が列名を直接参照する経路をそのまま使う（byte 不変）。合成 4 択
# （hl2/hlc3/ohlc4/hlcc4）は本結線層が共有 applied_price で列を先に合成し、その列名を price
# として渡す（tgp_btlm src は無改変・追加拡張のみ・非破壊）。moving_averages と同一の写像。
from common.applied_price import AppliedPrice, applied_price  # noqa: E402

_BTLM_SYNTHETIC_SOURCES = {
    "hl2": AppliedPrice.MEDIAN,
    "hlc3": AppliedPrice.TYPICAL,
    "ohlc4": AppliedPrice.OHLC4,
    "hlcc4": AppliedPrice.WEIGHTED,
}


def _resolve_btlm_price(df: Any, price: str) -> tuple[Any, str]:
    """tgp_btlm の price を 8 択解決する（結線拡張・src 無改変）。

    既存列（open/high/low/close 等）はコピーせず素通しし、build_btlm_bands の直接列参照を
    そのまま使う（byte 不変）。合成ソース（hl2/hlc3/ohlc4/hlcc4）は applied_price で列を合成し
    df のコピーへ一意列名で足し、その列名を返す。未知ソースは素通しし、build_btlm_bands の
    KeyError 契約に委ねる。
    """
    key = str(price).lower()
    lower = {str(c).lower(): c for c in df.columns}
    if key in lower:
        return df, price  # 既存列は素通し（byte 不変）
    kind = _BTLM_SYNTHETIC_SOURCES.get(key)
    if kind is None:
        return df, price  # 未知は build_btlm_bands の KeyError へ委ねる

    def col(name: str) -> Any:
        if name not in lower:
            raise KeyError(f"合成ソース計算に必要な列がありません: {name}")
        return df[lower[name]].to_numpy(dtype=float)

    series = applied_price(kind, col("open"), col("high"), col("low"), col("close"))
    col_name = f"_btlm_src_{key}"
    df2 = df.copy()
    df2[col_name] = series
    return df2, col_name


class _BindingSpec(TypedDict):
    """_TABLE のエントリ形状（compute_id+variant ごとの指標記述子）。

    loader      : add_* を遅延ロードする callable（指標 src 同名衝突を回避するため遅延）。
    output_kind : 系列 JSON 種別（"line" / "horizontal_line"・§6.3）。
    kind        : invoke 時の引数渡し（"btlm"=fitter 第3位置 / "kw"=df 以降キーワード専用）。
    latest_meta : Latest 増分計算メタの resolver（任意・ISSUE-097 🟡-6）。
                  params → (archetype, min_window, trailing_k)。未宣言は安全既定へ落ちる。
    preprocess  : invoke 前の kw 変換フック（任意・ISSUE-097 🟡-7）。(df, kw) → kw。
                  未宣言（既定 None）は変換なし。invoke から指標名直判定を排するための昇格点。
    time_required : time 列（time/date/DatetimeIndex）が必須か（任意・SOLID 是正 OCP-1）。
                  True の指標で時刻解決に失敗した KeyError は missing_time へ翻訳される。
                  未宣言（既定 False）の指標は missing_column 扱い。adapter のハードコード集合を
                  廃し本宣言を唯一の真実源とする（time 必須指標の追加で adapter 本体を改変しない）。
    params_defaults : param 既定値 {param_name: default}（ISSUE-180・OCP）。``GET /catalog`` が
                  配信する既定値の単一情報源。従来 ``catalog_schema.PARAM_DEFAULTS`` に別置き
                  されていた定義を本記述子へ集約し、catalog_schema は本宣言からの導出だけを行う
                  （指標追加時に既定値を別ファイルへ二重登録しない）。複数 variant を持つ
                  compute_id では **先頭 variant の 1 エントリにのみ** 宣言する（variant 間で
                  既定値は共有＝front の 1 指標 = 1 param セットと同一契約）。二重宣言・宣言漏れは
                  ``indicator_param_defaults`` が ValueError で検出する。
    """

    loader: Callable[[], Callable]
    output_kind: str
    kind: str
    latest_meta: NotRequired[Callable[[dict[str, Any]], tuple[str, int | None, int | None]]]
    preprocess: NotRequired[Callable[[Any, dict[str, Any]], dict[str, Any]]]
    thread_affinity: NotRequired[str]
    time_required: NotRequired[bool]
    params_defaults: NotRequired[dict[str, Any]]


# compute_id(+variant) → 指標記述子。loader は import を遅延し、指標 src 同名衝突を回避する。
#
# ISSUE-180（OCP）: 指標 1 件の追加で改変するファイルを減らすため、param 既定値
# （``params_defaults``）を本テーブルへ集約した。``catalog_schema.PARAM_DEFAULTS`` は本宣言からの
# 導出値であり、独立した定義を持たない。エントリの並び順は ``GET /catalog`` 応答の compute_id
# 出現順そのものであるため、既存応答の byte 等価を保つ目的で従来の配信順を維持する
# （並び替えは応答 JSON の key 順を変える＝挙動変更）。
_TABLE: dict[tuple[str, str], _BindingSpec] = {
    ("tgp_btlm", "default"): {
        "loader": lambda: _load_callable("tgp_btlm", "add_btlm"),
        "output_kind": "line", "kind": "btlm",
        # rpy2/R はスレッド親和（常に同一スレッドからの呼出）が必須＝専用ワーカーで実行する。
        #   未宣言の指標は純 numpy/pandas＝計算プールで並行実行してよい（SOLID 是正 🔴-3:
        #   スレッド親和性は HTTP 殻のハードコードでなく本テーブルの宣言で決まる）。
        "thread_affinity": "dedicated",
        # line 系（時系列トレンド線/帯）＝時刻軸必須。時刻解決失敗は missing_time へ翻訳される。
        "time_required": True,
        "params_defaults": {
            "fitter": "ols",
            "price": "open",
            "maxbars": 100,
            "q_low": 0.05,
            "q_high": 0.95,
            "mcmc_samples": "standard",
            "color": "rgba(123, 104, 238, 1)",
        },
    },
    ("btlm_trail", "default"): {
        "loader": lambda: _load_callable("btlm_trail", "add_btlm_trail"),
        "output_kind": "line", "kind": "kw",
        # ISSUE-233 S2/S3/S4: 窓末尾 OLS・経験分位・被覆率をいずれも「末尾 1 点だけ」計算する
        #   増分計算へ移す（従来は 1 ステップで窓全体を再計算し実測 334ms）。増分器が扱えない
        #   パラメータは従来経路（min_window=None＝full）で計算される＝挙動不変。
        "latest_meta": lambda params: ("incremental", None, 1, "btlm_trail"),
        "params_defaults": {
            "source": "close",
            "maxbars": 100,
            "q_low": 0.05,
            "q_high": 0.95,
            "band_method": "ols",
            "empirical_n": 500,
            "q_out": None,
            "show_metrics": True,
            "n_cov": 250,
            "color": "rgba(123, 104, 238, 1)",
        },
    },
    ("btlm_trail_marod", "default"): {
        "loader": lambda: _load_callable("btlm_trail_marod", "add_btlm_trail_marod"),
        "output_kind": "line", "kind": "kw",
        # ISSUE-233 S5: 因果ローリング分位バンド・イベント分位を末尾 1 点だけの計算へ移す。
        "latest_meta": lambda params: ("incremental", None, 1, "btlm_trail_marod"),
        "params_defaults": {
            "source": "close",
            "maxbars": 100,
            "q_low": 0.05,
            "q_high": 0.95,
            "q_out": 0.99,
            "k_events": 50,
            "event_agg": "episode",
            "window_n": 500,
            "color": "rgba(123, 104, 238, 1)",
        },
    },
    ("ma_marod", "default"): {
        "loader": lambda: _load_callable("ma_marod", "add_ma_marod"),
        "output_kind": "line", "kind": "kw",
        # ISSUE-233 S5: 因果ローリング分位バンド・イベント分位を末尾 1 点だけの計算へ移す。
        "latest_meta": lambda params: ("incremental", None, 1, "ma_marod"),
        "params_defaults": {
            "source": "close",
            "ma_type": "ema",
            "length": 50,
            "q_low": 0.05,
            "q_high": 0.95,
            "q_out": 0.99,
            "k_events": 50,
            "event_agg": "episode",
            "window_n": 500,
            "color": "rgba(255, 152, 0, 1)",
        },
    },
    # cvfe（条件付ボラティリティ予測 σ̂・別 pane オシレータ）。実バインディングは
    #   add_cvfe（indigators/cvfe/src/lwc_chart.py）。UI 計算経路が渡せるのは OHLC だけで
    #   仕様 §3.1 のティック列が無いため、§4.1-6 の FAIL 行が定める縮退
    #   （measure_id="PARK"）で算出する（精度は仕様 §7-6 のとおり低下する）。
    #   line 系（時系列）＝時刻軸必須。時刻解決失敗は missing_time へ翻訳される。
    # cvfe（条件付ボラティリティ予測 σ̂・価格スケール上の水平ダッシュ）。実バインディングは
    #   add_cvfe（indigators/cvfe/src/lwc_chart.py）。UI 計算経路が渡せるのは OHLC だけで
    #   仕様 §3.1 のティック列が無いため、§4.1-6 の FAIL 行が定める縮退
    #   （measure_id="PARK"）で算出する（精度は仕様 §7-6 のとおり低下・ISSUE-218）。
    #   line 系（時系列）＝時刻軸必須。時刻解決失敗は missing_time へ翻訳される。
    #
    #   公開パラメータは 6 個に絞る（認知負荷の最小化・ユーザー厳命 2026-07-30）。
    #   ここに無いパラメータは add_cvfe の既定値が使われる（refit_every=0・lam_gap=0.97・
    #   外れ値判定のしきい値群）。いずれも「既定から動かす根拠が無い」ことを実測または
    #   仕様で確認済み（詳細は catalog.js の CVFE 定義コメント）。
    ("cvfe", "default"): {
        "loader": lambda: _load_callable("cvfe", "add_cvfe"),
        "output_kind": "line", "kind": "kw",
        "time_required": True,
        "params_defaults": {
            "n_har": 500,
            "sigma_inner": 1.0,
            "sigma_outer": 2.0,
            "show_outliers": True,
            "display_mode": "dashes",
            "dash_opacity": 0.5,
        },
    },
    ("profit_band", "global"): {
        "loader": lambda: _load_callable("profit_band", "add_profit_band"),
        "output_kind": "line", "kind": "kw",
        # line 系（始値基準バンド）＝時刻軸必須。時刻解決失敗は missing_time へ翻訳される。
        "time_required": True,
        # params_defaults は compute_id 単位（variant 間で共有）。先頭 variant にのみ宣言する。
        "params_defaults": {
            "probabilities": [0.51, 0.8, 0.85, 0.9, 0.95, 0.98, 0.99],
            "buckets": ["nOH", "pOL", "pOH", "nOL"],
            "require_full": True,
            "legend": False,
            "normalize": "return",
            "window": "expanding",
            "atr_period": 14,
            "min_obs": 30,
        },
    },
    ("profit_band", "robust"): {
        "loader": lambda: _load_callable("profit_band", "add_robust_profit_band"),
        "output_kind": "line", "kind": "kw",
        "time_required": True,
    },
    ("price_range_power", "default"): {
        "loader": lambda: _load_callable("price_range_power", "add_price_range_power"),
        "output_kind": "horizontal_line", "kind": "kw",
        "latest_meta": _price_range_power_latest_meta,
        "preprocess": _prp_preprocess,
        "params_defaults": {
            "interval": 0.1,
            "range_from": None,
            "range_to": None,
            "top_n": 5,
            "width": 2,
            "bull_color": "rgba(46, 158, 91, 0.9)",
            "bear_color": "rgba(210, 67, 58, 0.9)",
        },
    },
    ("moving_averages", "default"): {
        "loader": lambda: _load_callable("moving_averages", "add_moving_averages"),
        "output_kind": "line", "kind": "kw",
        "latest_meta": _moving_averages_latest_meta,
        "params_defaults": {
            "ma_type": "ema",
            "length": 9,
            "source": "close",
            "offset": 0,
            "smoothing_type": "none",
            "smoothing_length": 9,
            "bb_stddev": 2.0,
            "timeframe": "chart",
            "wait_for_close": False,
        },
    },
    # --- profit_* 系（MQL 移植・lwc 仕様）。統合 FakeChart が line/histogram/水平線を
    #     一括収集するため output_kind は分岐に不使用（resolve 互換のため残置）。kind は全て kw。---
    ("profit_adx_needle", "default"): {
        "loader": lambda: _load_callable("profit_adx_needle", "add_adx_needle"),
        "output_kind": "histogram", "kind": "kw",
        "params_defaults": {
            "period": 6,
            "window": 120,
        },
    },
    ("profit_arctan", "default"): {
        "loader": lambda: _load_callable("profit_arctan", "add_arctan"),
        "output_kind": "histogram", "kind": "kw",
        "params_defaults": {
            "period": 6,
            "ma_method": 1,
            "bar_width": 0.1,
            "window": 120,
        },
    },
    ("profit_mfi", "default"): {
        "loader": lambda: _load_callable("profit_mfi", "add_mfi"),
        "output_kind": "line", "kind": "kw",
        "params_defaults": {
            "mfi_period": 14,
            "ma_period": 5,
        },
    },
    ("profit_rsi", "default"): {
        "loader": lambda: _load_callable("profit_rsi", "add_rsi"),
        "output_kind": "line", "kind": "kw",
        # ISSUE-249: 真の増分計算（状態器 "profit_rsi"）。従来は未宣言＝安全既定
        #   ("recurrence", None, 1) に落ち、末尾 1 点のために窓全体を再計算していた
        #   （実測 1386 本で 152.8ms・うち水準 152.3ms）。
        "latest_meta": lambda params: ("incremental", None, 1, "profit_rsi"),
        "params_defaults": {
            "rsi_period": 6,
            "apply": 5,
            # 正常帯（因果ローリング分位＝POT 閾値）と外れ値水準（経験的分位 / GPD 外挿）。
            #   既定は tickvol と同値（同じ意味の設定は指標間で同名・同既定）。
            "window_n": 500,
            "q_low": 0.10,
            "q_high": 0.90,
            "q_out": 0.99,
            "k_events": 50,
        },
    },
    ("profit_stc", "default"): {
        "loader": lambda: _load_callable("profit_stc", "add_stc"),
        "output_kind": "line", "kind": "kw",
        "params_defaults": {
            "period": 70,
        },
    },
    ("profit_oscillator", "default"): {
        "loader": lambda: _load_callable("profit_oscillator", "add_oscillator"),
        "output_kind": "histogram", "kind": "kw",
        "params_defaults": {
            "period_a": 6,
            "period_b": 60,
            "window": 120,
        },
    },
    ("profit_oscillator2", "default"): {
        "loader": lambda: _load_callable("profit_oscillator2", "add_oscillator2"),
        "output_kind": "histogram", "kind": "kw",
        "params_defaults": {
            "osc_period": 6,
            "stc_slow": 6,
            "ma_period": 60,
            "rci_period": 12,
            "direction": False,
        },
    },
    ("profit_osi_ma", "default"): {
        "loader": lambda: _load_callable("profit_osi_ma", "add_osi_ma"),
        "output_kind": "histogram", "kind": "kw",
        "params_defaults": {
            "ma_mode": 1,
            "ma_period": 21,
        },
    },
    ("profit_rmm", "default"): {
        "loader": lambda: _load_callable("profit_rmm", "add_rmm"),
        "output_kind": "histogram", "kind": "kw",
        "params_defaults": {
            "osc_period": 6,
            "ma_period": 6,
            "window": 120,
        },
    },
    ("profit_volatility", "default"): {
        "loader": lambda: _load_callable("profit_volatility", "add_volatility"),
        "output_kind": "line", "kind": "kw",
        "params_defaults": {
            "period": 6,
            "window": 120,
        },
    },
    ("profit_hl_band", "default"): {
        "loader": lambda: _load_callable("profit_hl_band", "add_hl_band"),
        "output_kind": "horizontal_line", "kind": "kw",
        "params_defaults": {
            "window": 120,
        },
    },
    ("profit_hlband", "separate"): {
        "loader": lambda: _load_callable("profit_hlband", "add_hlband_separate"),
        "output_kind": "histogram", "kind": "kw",
        # params_defaults は compute_id 単位（variant 間で共有）。先頭 variant にのみ宣言する。
        "params_defaults": {
            "draw_levels": True,
        },
    },
    ("profit_hlband", "overlay"): {
        "loader": lambda: _load_callable("profit_hlband", "add_hlband_overlay"),
        "output_kind": "horizontal_line", "kind": "kw",
    },
    ("profit_mfi_macd", "default"): {
        "loader": lambda: _load_callable("profit_mfi_macd", "add_mfimacd"),
        "output_kind": "histogram", "kind": "kw",
        "params_defaults": {
            "mfi_period": 13,
            "fast": 4,
            "slow": 8,
            "signal": 4,
        },
    },
    ("profit_rmm_macd", "default"): {
        "loader": lambda: _load_callable("profit_rmm_macd", "add_rmmmacd"),
        "output_kind": "histogram", "kind": "kw",
        "params_defaults": {
            "osc_period": 6,
            "ma_period": 6,
            "fast": 4,
            "slow": 8,
            "signal": 4,
            "window": 120,
        },
    },
    ("profit_rsi_macd", "default"): {
        "loader": lambda: _load_callable("profit_rsi_macd", "add_rsimacd"),
        "output_kind": "histogram", "kind": "kw",
        "params_defaults": {
            "rsi_period": 13,
            "fast": 4,
            "slow": 8,
            "signal": 4,
        },
    },
    # --- tickvol（ティックボリューム・専用ペインのヒストグラム＋外れ値水準線）-------
    #   本体は供給側 volume 列（＝当該足の tick 数）を加工せず描く点ごとの写像。水準線は
    #   POT（エピソード宣言クラスタリング）で作った同一観測集合の同一分位を、経験的分位と
    #   GPD の 2 通りで推定して並べる（indigators/tickvol/src/levels.py）。
    ("tickvol", "default"): {
        "loader": lambda: _load_callable("tickvol", "add_tickvol"),
        "output_kind": "histogram", "kind": "kw",
        "latest_meta": _tickvol_latest_meta,
        "params_defaults": {
            "window_n": 500,
            "q_low": 0.10,
            "q_high": 0.90,
            "q_out": 0.99,
            "k_events": 50,
            # 回帰トレンド（btlm_trail 仕様の参照拡張）は ISSUE-244 で UI から外した。
            #   計算は indigators/tickvol/src/trend.py にアーカイブとして残っている。
        },
    },
    # --- tickvol_updown は UI から外した（ISSUE-244）。パッケージ
    #   `indigators/tickvol_updown/` はアーカイブとして残す（同梱 ARCHIVE.md に復活手順）。
}


def indicator_param_defaults() -> dict[str, dict[str, Any]]:
    """_TABLE の ``params_defaults`` 宣言から compute_id → param 既定値を導出する（ISSUE-180）。

    ``catalog_schema.PARAM_DEFAULTS``（``GET /catalog`` の配信値）の唯一の生成元。返り値は deep copy
    のため、呼び出し側の変更は _TABLE へ波及しない。

    整合検査（宣言漏れ・二重宣言の構造的検出）:
      - _TABLE の compute_id は必ず 1 つの ``params_defaults`` 宣言を持つ（漏れは ValueError）。
      - 同一 compute_id の複数 variant が宣言することを禁ずる（二重定義の再発を ValueError で防ぐ）。
    dict の挿入順は _TABLE のエントリ順（＝従来の配信順）を保つ。
    """
    out: dict[str, dict[str, Any]] = {}
    for (compute_id, variant), spec in _TABLE.items():
        defaults = spec.get("params_defaults")
        if defaults is None:
            continue
        if compute_id in out:
            raise ValueError(
                f"params_defaults が重複宣言されています: {compute_id} (variant={variant})。"
                "compute_id ごとに先頭 variant の 1 エントリにのみ宣言してください。"
            )
        out[compute_id] = copy.deepcopy(defaults)
    missing = {compute_id for (compute_id, _variant) in _TABLE} - set(out)
    if missing:
        raise ValueError(
            f"params_defaults が未宣言の指標があります: {sorted(missing)}。"
            "_TABLE のエントリへ params_defaults を宣言してください。"
        )
    return out


# --- tgp::btlm の MCMC 設定（_TABLE 導出値 _DEFAULT_SAMPLES に依存するため _TABLE の後に置く）---
# tgp::btlm は MCMC（非決定的）。seed 未設定だと再当てはめ（ライブの毎分再計算）ごとに
# 結果が揺れ、トレンド線/帯が更新間で動いて見える。固定 seed で「同じ窓→毎回同一結果」にし、
# ライブ表示を静的表示と一致させる（rbridge は fit_predict ごとに set.seed する＝各 fit が決定的）。
# 値は任意だが固定であることが重要（再現性確保）。
_TGP_SEED = 20260101

# MCMC サンプル量プリセット（BTE=Burn-in, Total, Every）。Total を増やすほど posterior が
# 収束し分位帯が安定するが計算は重い（おおよそ Total 比例）。catalog.js の mcmc_samples と対応。
# ⚠️ 運用注意（性能）: server は R スレッド非安全のため単一スレッド（framework/server.py）。
#   tgp 計算中は全リクエストがブロックされる。ライブは 60 秒間隔で再計算するため、"max"（Total
#   4倍）は実 R btlm が 60 秒を超えると当該指標がライブ中ほとんど更新されない場合がある。
#   重い設定は静的分析向け。既定 standard は従来どおり軽量（後方互換）。
_BTE_PRESETS: dict[str, tuple[int, int, int]] = {
    "standard": (2000, 15000, 2),  # 既定（保持サンプル ~6500）
    "high": (4000, 30000, 2),      # ~13000・約2倍重い
    "max": (8000, 60000, 2),       # ~26000・約4倍重い（ライブ再計算で server をブロックし得る）
}
# 既定サンプル。param 既定値の単一情報源（_TABLE の tgp_btlm ``params_defaults``）から解決する
# （ISSUE-092 ③ / ISSUE-180・back 内二重定義の解消）。front（catalog.js）とは catalog_defaults.json
# 契約経由で back/front 双方のテストが一致を固定する。
_DEFAULT_SAMPLES = _TABLE[("tgp_btlm", "default")]["params_defaults"]["mcmc_samples"]


def _fitter_factory(name: str, samples: str = _DEFAULT_SAMPLES) -> Any:
    """fitter enum 文字列 → Fitter 実体（§3.3.3 fitter_factory）。

    "ols" → OlsBtlmFitter()、"tgp" → TgpBtlmFitter(seed=_TGP_SEED, bte=preset)。
    rpy2/R 不在でも TgpBtlmFitter の実体化自体は成功し、fit_predict 時に ImportError。
    tgp は MCMC のため seed を固定し（再現性確保）、``samples`` で BTE プリセットを選んで
    分位帯の安定性を調整する。未知の ``samples`` は standard へフォールバック。``ols`` は
    解析解のため ``samples`` を無視する。
    """
    src = _load_src_package("tgp_btlm")
    if name == "ols":
        return src.OlsBtlmFitter()
    if name == "tgp":
        bte = _BTE_PRESETS.get(samples, _BTE_PRESETS[_DEFAULT_SAMPLES])
        return src.TgpBtlmFitter(seed=_TGP_SEED, bte=bte)
    raise ValueError(f"未知の fitter です: {name}")


def requires_dedicated_worker(indicator_id: "str | None") -> bool:
    """指標がスレッド親和専用ワーカーでの実行を要するか（SOLID 是正 🔴-3・宣言参照）。

    _TABLE の ``thread_affinity: "dedicated"`` 宣言を唯一の真実源とする（HTTP 殻は本関数を
    呼ぶだけで指標名を知らない）。未知 id・未宣言は False＝計算プールで並行実行してよい。
    """
    if not indicator_id:
        return False
    return any(
        spec.get("thread_affinity") == "dedicated"
        for (cid, _variant), spec in _TABLE.items()
        if cid == indicator_id
    )


def requires_time(compute_id: "str | None") -> bool:
    """指標が time 列（time/date/DatetimeIndex）を必須とするか（SOLID 是正 OCP-1・宣言参照）。

    _TABLE の ``time_required: True`` 宣言を唯一の真実源とする（adapter は集合ハードコードでなく
    本関数を呼ぶだけで指標名を知らない）。time 必須指標の追加時に adapter 本体の改変は不要。
    未知 id・未宣言・空/None は False（missing_column 扱い）。いずれかの variant が True なら True。
    """
    if not compute_id:
        return False
    return any(
        spec.get("time_required") is True
        for (cid, _variant), spec in _TABLE.items()
        if cid == compute_id
    )


def latest_meta_fields(
    compute_id: str, variant: str, params: dict[str, Any]
) -> tuple[str, int | None, int | None] | None:
    """_BindingSpec の ``latest_meta`` 宣言から (archetype, min_window, trailing_k) を解決する。

    ISSUE-097 🟡-6: archetype 分類の単一情報源。未登録 (compute_id, variant) または
    ``latest_meta`` 未宣言のエントリは ``None`` を返し、呼び出し側（latest_meta.py）が
    安全既定 recurrence/full/K=1 へ落とす（従来の未登録安全既定と同一挙動）。
    """
    spec = _TABLE.get((compute_id, variant))
    resolver = spec.get("latest_meta") if spec is not None else None
    if resolver is None:
        return None
    return resolver(params)


@dataclass(frozen=True)
class CallBinding:
    """1 指標(+variant)の呼出規約。``invoke`` で既存 add_* を呼ぶ。"""

    compute_id: str
    variant: str
    output_kind: str
    _kind: str  # "btlm"（fitter 第3位置）/ "kw"（df 以降キーワード専用）

    @classmethod
    def resolve(cls, compute_id: str, variant: str) -> "CallBinding":
        """compute_id(+variant) から規約を解決する。未知は KeyError（§3.3.3）。"""
        spec = _TABLE[(compute_id, variant)]
        return cls(compute_id, variant, spec["output_kind"], spec["kind"])

    def invoke(self, chart: Any, df: Any, params: dict[str, Any]) -> None:
        """既存 add_* を CALL_BINDING に従い呼ぶ（描画せず chart へ収集）。

        btlm: ``add_btlm(chart, df, <fitter実体>, **kw)``（fitter は第3位置・§5.5.4.1）。
        kw  : ``add_*(chart, df, **kw)``（df 以降キーワード専用）。
        """
        spec = _TABLE[(self.compute_id, self.variant)]
        callable_ = spec["loader"]()
        if self._kind == "btlm":
            kw = dict(params)
            # mcmc_samples は fitter 構築用（add_btlm の kwarg ではない）→ pop して factory へ。
            fitter = _fitter_factory(kw.pop("fitter"), kw.pop("mcmc_samples", _DEFAULT_SAMPLES))
            # ソース 8 択化: 合成ソースは applied_price で列合成し price を差し替える（src 無改変）。
            df, kw["price"] = _resolve_btlm_price(df, kw.get("price", "open"))
            callable_(chart, df, fitter, **_accepted_kwargs(callable_, kw))
        else:
            kw = _accepted_kwargs(callable_, params)
            # 指標固有の前処理は _BindingSpec の preprocess フックへ委譲（compute_id 直判定を
            # invoke から排除・ISSUE-097 🟡-7）。未宣言指標はフック無し＝変換なし。
            preprocess = spec.get("preprocess")
            if preprocess is not None:
                kw = preprocess(df, kw)
            callable_(chart, df, **kw)
