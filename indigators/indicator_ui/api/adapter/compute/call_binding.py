"""CALL_BINDING（内部設計書 §3.3.3・基本設計 §5.5.4.1）— 指標記述子表と結線。

compute_id(+variant) → {callable, output_kind, keyword_params} を保持し、``invoke`` で
既存 add_* を一意・決定論的に呼ぶ。

**本モジュールの責務は「表」と「表からの導出」だけである**（ISSUE-502 段階 4B・SRP）。
指標固有の知識も汎用機構の実装も持たない。分離先は次のとおり:

  | 責務                          | 所有者                                        |
  |---|---|
  | 指標 src のロード境界          | ``adapter.compute.src_packages``              |
  | param 既定値の導出・束縛       | ``adapter.compute.param_binding``             |
  | ``kind`` ごとの呼出器（汎用）  | ``adapter.compute.kind_invokers``             |
  | 指標固有 hook（1 指標 1 本）   | ``adapter.compute.bindings.<compute_id>``     |

指標を 1 件追加する手順（ISSUE-180・ISSUE-502 段階 4B・back 側）:
    1. ``_TABLE`` へ ``(compute_id, variant)`` のエントリを 1 件足す。呼出規約（loader /
       output_kind / kind）に加え、必要なら thread_affinity / time_required / latest_meta /
       preprocess を、そして param 既定値 ``params_defaults`` を **同一エントリ内に** 宣言する。
       ``params_defaults`` は **その variant の add_* が実際に受理する引数** を宣言する
       （ISSUE-278 #8）。variant を複数持つ指標は各 variant がそれぞれ宣言する（受理引数は
       variant ごとに異なるため。宣言と実シグネチャの一致は
       ``api/tests/test_call_binding_param_scopes.py`` が固定する）。
    2. hook（latest_meta の解決規則・preprocess・専用例外型・共有既定値など）が要るなら
       ``adapter/compute/bindings/<compute_id>.py`` を **1 本置く**。登録行は不要で
       （``bindings.__getattr__`` が遅延解決する）、``_TABLE`` の宣言から参照するだけでよい。
       **本ファイルへ関数・定数・import・再エクスポート別名を足してはならない**
       （``api/tests/test_call_binding_open_closed.py`` の G1〜G4 が AST で固定する）。
    3. back 側の改変はこれで完了する。``catalog_schema.PARAM_DEFAULTS``（``GET /catalog`` の
       配信値）・``requires_time`` ・``requires_dedicated_worker`` ・``latest_meta`` はいずれも
       本エントリからの導出であり、追加登録は不要（宣言漏れは
       ``indicator_param_defaults`` が ValueError で、テストが構造検査で検出する）。
    4. 既定値を追加・変更したときは front 同期契約 ``api/tests/golden/catalog_defaults.json``
       を更新する（back 配信値 == front 静的フォールバック値のオラクル）。
    5. front（``web/js/usecase/catalog.js`` の IndicatorDef、足内更新対象なら
       ``intrabar_forming_ids.js``）は別アクターの所有物であり、本テーブルからは導出されない
       （``GET /catalog`` は param 既定値のみを配信する契約のため）。front 側の宣言は別途必要。

なお **新しい引数渡し規約（``kind``）を足すとき**だけは ``_INVOKERS`` へ 1 行足す。これは
「指標が増える」軸ではなく「呼出規約が増える」軸であり、両者は独立に変化する。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, NotRequired, TypedDict

from adapter.compute import bindings
from adapter.compute import kind_invokers
from adapter.compute.latest_meta_spec import LatestMeta
from adapter.compute.kind_invokers import Invoker as _Invoker

# 汎用機構の再エクスポート（既存の import 面を維持する。いずれも指標名を含まない汎用名であり、
#   指標を足しても増減しない＝OCP ガードの対象外）。
from adapter.compute.param_binding import (
    CALC_TIMEFRAME_DEFAULT,
    LAYER_CONSUMED_PARAMS,
    accepted_param_names,
    bind_kwargs as _bind_kwargs,
    derive_param_defaults as _derive_param_defaults,
    derive_param_scopes as _derive_param_scopes,
)
from adapter.compute.src_packages import (
    indicator_src,
    load_callable as _load_callable,
    load_src_package as _load_src_package,
)

__all__ = [
    "CALC_TIMEFRAME_DEFAULT",
    "CallBinding",
    "LAYER_CONSUMED_PARAMS",
    "accepted_param_names",
    "indicator_param_defaults",
    "indicator_param_scopes",
    "indicator_src",
    "latest_meta_fields",
    "requires_dedicated_worker",
    "requires_time",
    "value_error_declarations",
    "value_error_types",
]


class _BindingSpec(TypedDict):
    """_TABLE のエントリ形状（compute_id+variant ごとの指標記述子）。

    loader      : add_* を遅延ロードする callable（指標 src 同名衝突を回避するため遅延）。
    output_kind : 系列 JSON 種別（"line" / "horizontal_line"・§6.3）。
    kind        : invoke 時の引数渡し（"btlm"=fitter 第3位置 / "kw"=df 以降キーワード専用）。
    latest_meta : Latest 増分計算メタの resolver（任意・ISSUE-097 🟡-6）。
                  params → LatestMeta。未宣言は安全既定へ落ちる。宣言が一行の定数返しで済まない
                  指標は協働子 ``bindings.<compute_id>.latest_meta`` を参照する。
    preprocess  : invoke 前の kw 変換フック（任意・ISSUE-097 🟡-7）。(df, kw) → kw。
                  未宣言（既定 None）は変換なし。invoke から指標名直判定を排するための昇格点。
                  実装は協働子 ``bindings.<compute_id>`` が所有する。
    value_error_types : ValueError の下位型 → error.type の宣言（任意・SOLID 是正 OCP-3）。
                  ``{error_type: 型ローダ}``。指標 src が専用例外型を持ち、素の ValueError と
                  区別して翻訳させたいときだけ宣言する（例: profit_band の EmptyBucketError →
                  ``empty_series``）。型は遅延ロード（指標 src の import を宣言時に強制しない）。
                  未宣言の指標は一様に ``validation`` へ翻訳される。``requires_time`` と同型に、
                  adapter のハードコードを廃して本宣言を唯一の真実源とする（指標名リテラルが
                  adapter から消える）。
    time_required : time 列（time/date/DatetimeIndex）が必須か（任意・SOLID 是正 OCP-1）。
                  True の指標で時刻解決に失敗した KeyError は missing_time へ翻訳される。
                  未宣言（既定 False）の指標は missing_column 扱い。adapter のハードコード集合を
                  廃し本宣言を唯一の真実源とする（time 必須指標の追加で adapter 本体を改変しない）。
    params_defaults : param 既定値 {param_name: default}（ISSUE-180・OCP）。``GET /catalog`` が
                  配信する既定値の単一情報源。従来 ``catalog_schema.PARAM_DEFAULTS`` に別置き
                  されていた定義を本記述子へ集約し、catalog_schema は本宣言からの導出だけを行う
                  （指標追加時に既定値を別ファイルへ二重登録しない）。
                  宣言粒度は **variant**（ISSUE-278 #8）。宣言するキー集合＝その variant の
                  add_* が受理する引数であり、``GET /catalog`` の ``paramScopes`` としてそのまま
                  配信される（front はこれで variant ごとの表示・送信を決める）。共有 param は
                  各 variant のエントリが同じ既定値で宣言する（食い違いと宣言漏れは
                  ``indicator_param_defaults`` が ValueError で検出する）。複数 variant が同じ
                  リテラルを 2 度書かないよう、共有分は協働子（例
                  ``bindings.profit_band.SHARED_PARAMS_DEFAULTS``）が所有する。
    """

    loader: Callable[[], Callable]
    output_kind: str
    kind: str
    # ISSUE-278 #7: 位置タプル（3 or 4 要素）をやめ LatestMeta を返す。タプルだと宣言型が
    #   3 要素のままで 4 要素目（増分器名）の書き忘れを型検査が通し、実行時は例外なく
    #   full 再計算へ縮退していた（値は正しいまま性能だけ落ちるので検定も緑）。
    latest_meta: NotRequired[Callable[[dict[str, Any]], LatestMeta]]
    preprocess: NotRequired[Callable[[Any, dict[str, Any]], dict[str, Any]]]
    thread_affinity: NotRequired[str]
    time_required: NotRequired[bool]
    value_error_types: NotRequired[dict[str, Callable[[], type]]]
    params_defaults: NotRequired[dict[str, Any]]


# compute_id(+variant) → 指標記述子。loader は import を遅延し、指標 src 同名衝突を回避する。
#
# ISSUE-180（OCP）: 指標 1 件の追加で改変するファイルを減らすため、param 既定値
# （``params_defaults``）を本テーブルへ集約した。``catalog_schema.PARAM_DEFAULTS`` は本宣言からの
# 導出値であり、独立した定義を持たない。エントリの並び順は ``GET /catalog`` 応答の compute_id
# 出現順そのものであるため、既存応答の byte 等価を保つ目的で従来の配信順を維持する
# （並び替えは応答 JSON の key 順を変える＝挙動変更）。
#
# ISSUE-502 段階 4B（SRP/OCP）: 本テーブルは **宣言だけ** を持つ。指標固有の手続き
# （fitter 構築・合成ソース解決・専用例外型のロード・共有既定値・増分メタの解決規則）は
# すべて ``bindings.<compute_id>`` が所有し、ここからは名前で参照する。
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
            # MCMC サンプル量の既定は協働子が所有する（BTE プリセット表と同じ場所に置き、
            #   同じリテラルを 2 度書かない）。配信値との一致は test_catalog_schema が固定する。
            "mcmc_samples": bindings.tgp_btlm.DEFAULT_SAMPLES,
            "color": "rgba(123, 104, 238, 1)",
        },
    },
    ("btlm_trail", "default"): {
        "loader": lambda: _load_callable("btlm_trail", "add_btlm_trail"),
        "output_kind": "line", "kind": "kw",
        # ISSUE-233 S2/S3/S4: 窓末尾 OLS・経験分位・被覆率をいずれも「末尾 1 点だけ」計算する
        #   増分計算へ移す（従来は 1 ステップで窓全体を再計算し実測 334ms）。増分器が扱えない
        #   パラメータは従来経路（min_window=None＝full）で計算される＝挙動不変。
        "latest_meta": lambda params: LatestMeta("incremental", None, 1, "btlm_trail"),
        "params_defaults": {
            "source": "close",
            "maxbars": 100,
            "q_low": 0.05,
            "q_high": 0.95,
            "band_method": "ols",
            "empirical_n": 500,
            "q_out": None,
            # 較正基準（正本仕様 §2(b')・ISSUE-495）: close＝終値乖離（既定・現行）／
            #   hl＝下側は安値・上側は高値の乖離分布（経験分位のみ・実績率もヒゲ非貫通率へ）。
            "band_basis": "close",
            "show_metrics": True,
            "n_cov": 250,
            "color": "rgba(123, 104, 238, 1)",
        },
    },
    ("btlm_trail_marod", "default"): {
        "loader": lambda: _load_callable("btlm_trail_marod", "add_btlm_trail_marod"),
        "output_kind": "line", "kind": "kw",
        # ISSUE-233 S5: 因果ローリング分位バンド・イベント分位を末尾 1 点だけの計算へ移す。
        "latest_meta": lambda params: LatestMeta("incremental", None, 1, "btlm_trail_marod"),
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
        "latest_meta": lambda params: LatestMeta("incremental", None, 1, "ma_marod"),
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
    # cvfe（条件付ボラティリティ予測 σ̂・価格スケール上の水平ダッシュ）。実バインディングは
    #   add_cvfe（indigators/cvfe/src/lwc_chart.py）。UI 計算経路が渡せるのは OHLC だけで
    #   仕様 §3.1 のティック列が無いため、§4.1-6 の FAIL 行が定める縮退
    #   （measure_id="PARK"）で算出する（精度は仕様 §7-6 のとおり低下する・ISSUE-218）。
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
        # SOLID 是正 OCP-3: 「必須バケット空」は専用型 EmptyBucketError（ValueError サブクラス）で
        #   送出される。素の ValueError（normalize 不正等）と区別して empty_series へ翻訳する。
        "value_error_types": {"empty_series": bindings.profit_band.empty_bucket_error},
        # ISSUE-278 #8: global が受理するのは共有 3 件＋ require_full。robust 専用
        #   （normalize/window/atr_period/min_obs）は add_profit_band のシグネチャに無い。
        "params_defaults": {
            **bindings.profit_band.SHARED_PARAMS_DEFAULTS,
            "require_full": True,
        },
    },
    ("profit_band", "robust"): {
        "loader": lambda: _load_callable("profit_band", "add_robust_profit_band"),
        "output_kind": "line", "kind": "kw",
        "time_required": True,
        "value_error_types": {"empty_series": bindings.profit_band.empty_bucket_error},
        # ISSUE-278 #8: robust が受理するのは共有 3 件＋因果窓/正規化の 4 件。
        #   ``require_full`` は add_robust_profit_band のシグネチャに無い（global 専用）。
        "params_defaults": {
            **bindings.profit_band.SHARED_PARAMS_DEFAULTS,
            "normalize": "return",
            "window": "expanding",
            "atr_period": 14,
            "min_obs": 30,
        },
    },
    ("price_range_power", "default"): {
        "loader": lambda: _load_callable("price_range_power", "add_price_range_power"),
        "output_kind": "horizontal_line", "kind": "kw",
        "latest_meta": bindings.price_range_power.latest_meta,
        "preprocess": bindings.price_range_power.preprocess,
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
        "latest_meta": bindings.moving_averages.latest_meta,
        "params_defaults": {
            "ma_type": "ema",
            "length": 9,
            "source": "close",
            "offset": 0,
            "smoothing_type": "none",
            "smoothing_length": 9,
            "bb_stddev": 2.0,
        },
    },
    # --- period_hl / ytd_hl（期間高安・年初来高安・ISSUE-490）。実バインディングは
    #     add_period_hl / add_ytd_hl（indigators/period_hl/src/lwc_chart.py・1 パッケージ
    #     2 compute_id）。価格ラダーの水準供給が目的: period_hl は各バーの high / low
    #     そのもの（末尾＝形成中バー＝その時間足の進行中期間の走行高安。期間境界は
    #     ロールアップのグリッドを継承し、本指標は境界の定義を持たない）。ytd_hl は
    #     暦年内の走行 max / min（窓の最初の年は NaN＝年初被覆を保証できないため）。
    #     公開パラメータは無し（認知負荷の最小化・水準の定義に自由度が無い）。
    ("period_hl", "default"): {
        "loader": lambda: _load_callable("period_hl", "add_period_hl"),
        "output_kind": "line", "kind": "kw",
        "time_required": True,
        "params_defaults": {},
    },
    ("ytd_hl", "default"): {
        "loader": lambda: _load_callable("period_hl", "add_ytd_hl"),
        "output_kind": "line", "kind": "kw",
        "time_required": True,
        "params_defaults": {},
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
        "latest_meta": lambda params: LatestMeta("incremental", None, 1, "profit_rsi"),
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
        "params_defaults": {
            "draw_levels": True,
        },
    },
    ("profit_hlband", "overlay"): {
        "loader": lambda: _load_callable("profit_hlband", "add_hlband_overlay"),
        "output_kind": "horizontal_line", "kind": "kw",
        # ISSUE-278 #8: overlay は指標固有 param を受理しない（add_hlband_overlay の
        #   シグネチャに draw_levels は無い＝separate 専用）。空宣言は「受理引数なし」の明示。
        "params_defaults": {},
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
        "latest_meta": bindings.tickvol.latest_meta,
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


# --- kind（引数渡し規約）ごとの呼出器（SOLID 是正 OCP-2）------------------------------
# ``kind`` は _BindingSpec の宣言として残す（catalog / scope 検定が読む面）。従来 ``invoke`` が
# ``if self._kind == "btlm"`` で分岐していた部分だけを表 ``_INVOKERS`` へ移し、invoke は分岐を
# 持たない（新しい引数渡し規約は本表へ 1 行足すだけで足りる＝invoke 本体を改変しない）。
#
# 本表は「呼出規約が増える」軸でだけ変化する（指標が増える軸とは独立）。実装は所有者が持つ:
#   kw   … どの指標にも属さない汎用規約 → adapter.compute.kind_invokers
#   btlm … tgp_btlm だけが要する規約     → adapter.compute.bindings.tgp_btlm
_INVOKERS: dict[str, _Invoker] = {
    "btlm": bindings.tgp_btlm.INVOKER,
    "kw": kind_invokers.KW_INVOKER,
}

#: ``kind`` ごとに ``invoke`` 自身が消費する param（add_* の kwarg ではない）。
#: _INVOKERS からの**導出値**であり独立した宣言を持たない（scope 検定が読む面は不変）。
_KIND_CONSUMED_PARAMS: dict[str, frozenset[str]] = {
    kind: invoker.consumes for kind, invoker in _INVOKERS.items()
}


# --- 表からの導出（宣言を読むだけ・指標名を知らない）---------------------------------
def indicator_param_defaults() -> dict[str, dict[str, Any]]:
    """_TABLE の ``params_defaults`` 宣言から compute_id → param 既定値を導出する（ISSUE-180）。

    ``catalog_schema.PARAM_DEFAULTS``（``GET /catalog`` の配信値）の唯一の生成元。
    導出規則そのものは ``_derive_param_defaults``（adapter.compute.param_binding）が所有する。
    """
    return _derive_param_defaults(_TABLE)


def indicator_param_scopes() -> dict[str, dict[str, list[str]]]:
    """compute_id → variant → その variant が受理する param 名（ISSUE-278 #8）。

    ``GET /catalog`` が ``paramScopes`` として配信する。導出規則そのものは
    ``_derive_param_scopes``（adapter.compute.param_binding）が所有する。
    """
    return _derive_param_scopes(_TABLE)


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


def value_error_declarations() -> dict[str, dict[str, Callable[[], type]]]:
    """_TABLE の ``value_error_types`` 宣言を compute_id 単位へ導出する（SOLID 是正 OCP-3）。

    同一 compute_id の複数 variant が宣言する場合は和を取る（profit_band の global/robust は
    どちらも EmptyBucketError を送出しうるため同一宣言を持つ）。宣言を持たない指標は本 dict に
    現れない＝汎用 ``validation`` へ一様翻訳される。
    """
    out: dict[str, dict[str, Callable[[], type]]] = {}
    for (compute_id, _variant), spec in _TABLE.items():
        declared = spec.get("value_error_types")
        if declared:
            out.setdefault(compute_id, {}).update(declared)
    return out


def value_error_types(compute_id: "str | None") -> dict[str, Callable[[], type]]:
    """指標が宣言する「ValueError 下位型 → error.type」（未宣言・未知 id は空 dict）。

    adapter はこの宣言だけを見て翻訳する（指標名も専用例外型も adapter には現れない）。
    """
    if not compute_id:
        return {}
    return value_error_declarations().get(compute_id, {})


def latest_meta_fields(
    compute_id: str, variant: str, params: dict[str, Any]
) -> "LatestMeta | None":
    """_BindingSpec の ``latest_meta`` 宣言から増分計算メタを解決する。

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

        引数渡しの規約（``kind``）ごとの差は ``_INVOKERS`` 表が持ち、本メソッドは分岐しない
        （btlm: fitter を第 3 位置・§5.5.4.1／kw: df 以降キーワード専用）。
        """
        spec = _TABLE[(self.compute_id, self.variant)]
        callable_ = spec["loader"]()
        # 計算層が消費する param（計算.時間足）は指標 src へ渡さない。従来は
        #   _accepted_kwargs の無言破棄に頼っていたが、無言破棄を廃した（ISSUE-278 #8）ため
        #   ここで明示的に取り除く（mcmc_samples の pop と同じ規律）。
        kw = {k: v for k, v in params.items() if k not in LAYER_CONSUMED_PARAMS}
        invoker = _INVOKERS[self._kind]
        # kind ごとに invoke 自身が消費する param（add_* の kwarg ではない）を取り除く。
        #   消費集合は _INVOKERS が唯一の宣言（scope 検定は導出値 _KIND_CONSUMED_PARAMS を読む）。
        consumed = {k: kw.pop(k) for k in invoker.consumes if k in kw}
        invoker.call(spec, callable_, chart, df, kw, consumed)
