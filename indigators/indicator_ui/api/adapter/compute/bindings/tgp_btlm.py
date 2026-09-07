"""tgp_btlm の呼出規約フック（fitter 実体化・ソース 8 択解決・btlm 引数渡し規約）。

本モジュールは call_binding から分離した協働子である（ISSUE-502 段階 4B・SRP/OCP）。
call_binding は ``_TABLE`` の宣言と ``_INVOKERS`` の 1 行で本モジュールを参照するだけで、
MCMC プリセットも seed も合成ソースの列合成規則も知らない。

所有する知識（＝tgp_btlm の都合が変わったとき改変されるのは本ファイルだけ）:
  - ``SEED`` / ``BTE_PRESETS`` / ``DEFAULT_SAMPLES`` : MCMC 設定
  - ``FITTERS`` / ``fitter_factory``                 : fitter enum 文字列 → Fitter 実体
  - ``SYNTHETIC_SOURCES`` / ``resolve_price``        : price の 8 択解決（結線拡張・src 無改変）
  - ``INVOKER``                                      : kind="btlm" の呼出器（fitter を第 3 位置引数へ）
"""

from __future__ import annotations

from types import ModuleType
from typing import Any, Callable

from adapter.compute.kind_invokers import Invoker
from adapter.compute.param_binding import bind_kwargs
from adapter.compute.src_packages import load_src_package
from common.applied_price import (
    OHLC_COLUMNS,
    SYNTHETIC_SOURCE_TO_APPLIED,
    applied_price,
)

# --- MCMC 設定 ------------------------------------------------------------- #
# tgp::btlm は MCMC（非決定的）。seed 未設定だと再当てはめ（ライブの毎分再計算）ごとに
# 結果が揺れ、トレンド線/帯が更新間で動いて見える。固定 seed で「同じ窓→毎回同一結果」にし、
# ライブ表示を静的表示と一致させる（rbridge は fit_predict ごとに set.seed する＝各 fit が決定的）。
# 値は任意だが固定であることが重要（再現性確保）。
SEED = 20260101

# MCMC サンプル量プリセット（BTE=Burn-in, Total, Every）。Total を増やすほど posterior が
# 収束し分位帯が安定するが計算は重い（おおよそ Total 比例）。catalog.js の mcmc_samples と対応。
# ⚠️ 運用注意（性能）: server は R スレッド非安全のため単一スレッド（framework/server.py）。
#   tgp 計算中は全リクエストがブロックされる。ライブは 60 秒間隔で再計算するため、"max"（Total
#   4倍）は実 R btlm が 60 秒を超えると当該指標がライブ中ほとんど更新されない場合がある。
#   重い設定は静的分析向け。既定 standard は従来どおり軽量（後方互換）。
BTE_PRESETS: dict[str, tuple[int, int, int]] = {
    "standard": (2000, 15000, 2),  # 既定（保持サンプル ~6500）
    "high": (4000, 30000, 2),      # ~13000・約2倍重い
    "max": (8000, 60000, 2),       # ~26000・約4倍重い（ライブ再計算で server をブロックし得る）
}

#: 既定サンプル。``GET /catalog`` が配信する ``mcmc_samples`` 既定値の**単一情報源**であり、
#: ``call_binding._TABLE`` の tgp_btlm エントリが本定数を参照する（同じリテラルを 2 度書かない）。
#: 従来は逆向き（_TABLE から導出）だったため、_TABLE の後ろでしか定義できず tgp 固有コードが
#: call_binding に居座る原因になっていた。参照の向きを反転しても値は同一・二重定義も生じない
#: （api/tests/test_catalog_schema.py が PARAM_DEFAULTS との一致を固定する）。front（catalog.js）
#: とは golden の catalog_defaults.json 契約経由で back/front 双方のテストが一致を固定する。
DEFAULT_SAMPLES = "standard"


def _make_ols_fitter(src: ModuleType, samples: str) -> Any:
    """解析解の OLS fitter（``samples`` は無関係＝MCMC を持たない）。"""
    del samples
    return src.OlsBtlmFitter()


def _make_tgp_fitter(src: ModuleType, samples: str) -> Any:
    """MCMC の tgp fitter。seed 固定（再現性）＋ ``samples`` で BTE プリセットを選ぶ。

    未知の ``samples`` は standard へフォールバックする（従来不変）。
    """
    return src.TgpBtlmFitter(seed=SEED, bte=BTE_PRESETS.get(samples, BTE_PRESETS[DEFAULT_SAMPLES]))


#: fitter enum 文字列 → 実体化（SOLID 是正 OCP-1）。fitter を 1 種増やす手順は本表へ 1 行足すこと
#: だけであり、``fitter_factory`` 本体は改変しない（本体に比較が 0 件であることを
#: ``api/tests/test_call_binding_open_closed.py`` が AST で固定する）。
FITTERS: dict[str, Callable[[ModuleType, str], Any]] = {
    "ols": _make_ols_fitter,
    "tgp": _make_tgp_fitter,
}


def fitter_factory(name: str, samples: str = DEFAULT_SAMPLES) -> Any:
    """fitter enum 文字列 → Fitter 実体（§3.3.3 fitter_factory・``FITTERS`` 表引き）。

    rpy2/R 不在でも TgpBtlmFitter の実体化自体は成功し、fit_predict 時に ImportError。
    未知の fitter 名は ValueError（文言は従来と同一）。
    """
    src = load_src_package("tgp_btlm")
    try:
        make = FITTERS[name]
    except KeyError:
        raise ValueError(f"未知の fitter です: {name}") from None
    return make(src, samples)


# --- price のソース 8 択解決 ------------------------------------------------ #
# tgp_btlm ソース 8 択化（kind-twirling-hollerith.md §4）。既存 4 択（open/high/low/close）は
# 参照実装 build_btlm_bands が列名を直接参照する経路をそのまま使う（byte 不変）。合成 4 択
# （hl2/hlc3/ohlc4/hlcc4）は本結線層が共有 applied_price で列を先に合成し、その列名を price
# として渡す（tgp_btlm src は無改変・追加拡張のみ・非破壊）。moving_averages と同一の写像。

#: 合成が要る source → 種別。以前は逐語列挙しており共有表 ``SOURCE_TO_APPLIED``（8 組）の
#: 部分写しになっていた（ISSUE-502 段階 2 D-6: 8 択解決の第 4 の部分実装）。列挙をやめ共有側の
#: 導出値をそのまま束縛する（source を 1 つ足しても本ファイルは改変不要）。
SYNTHETIC_SOURCES = SYNTHETIC_SOURCE_TO_APPLIED


def resolve_price(df: Any, price: str) -> tuple[Any, str]:
    """tgp_btlm の price を 8 択解決する（結線拡張・src 無改変）。

    既存列（open/high/low/close 等）はコピーせず素通しし、build_btlm_bands の直接列参照を
    そのまま使う（byte 不変）。合成ソース（hl2/hlc3/ohlc4/hlcc4）は applied_price で列を合成し
    df のコピーへ一意列名で足し、その列名を返す。未知ソースは素通しし、build_btlm_bands の
    KeyError 契約に委ねる。

    共有の解決手続き common.applied_price.resolve_source_prices へは寄せていない（ISSUE-502 段階 2 D-6
    で差分を実測）。契約が別物であるため:
        * 戻り値が価格配列ではなく ``(df, 列名)``（tgp_btlm src は列名で直接参照する＝byte 不変）。
        * 既存列は**素通し**し合成しない（price="close" は同名列をそのまま使う）。
        * 列欠落は ``ValueError`` ではなく ``KeyError``（build_btlm_bands の契約に揃える）。
    語彙（どの source が合成を要するか・どの列が要るか）だけを共有側から受け取る。
    """
    key = str(price).lower()
    lower = {str(c).lower(): c for c in df.columns}
    if key in lower:
        return df, price  # 既存列は素通し（byte 不変）
    kind = SYNTHETIC_SOURCES.get(key)
    if kind is None:
        return df, price  # 未知は build_btlm_bands の KeyError へ委ねる

    def col(name: str) -> Any:
        if name not in lower:
            raise KeyError(f"合成ソース計算に必要な列がありません: {name}")
        return df[lower[name]].to_numpy(dtype=float)

    series = applied_price(kind, *(col(name) for name in OHLC_COLUMNS))
    col_name = f"_btlm_src_{key}"
    df2 = df.copy()
    df2[col_name] = series
    return df2, col_name


# --- kind="btlm" の引数渡し規約 --------------------------------------------- #
def call_btlm(spec, callable_, chart, df, kw, consumed) -> None:
    """add_btlm(chart, df, <fitter実体>, **kw)（fitter は第 3 位置・§5.5.4.1）。

    ``fitter_factory`` は**本モジュールの globals 経由**で呼ぶ（既存テストの monkeypatch 面。
    属性束縛にすると差し替えが効かなくなる）。差し替え点は本モジュール 1 つだけである。
    """
    del spec
    fitter = fitter_factory(consumed["fitter"], consumed.get("mcmc_samples", DEFAULT_SAMPLES))
    # ソース 8 択化: 合成ソースは applied_price で列合成し price を差し替える（src 無改変）。
    df, kw["price"] = resolve_price(df, kw.get("price", "open"))
    callable_(chart, df, fitter, **bind_kwargs(callable_, kw))


#: kind="btlm" の呼出器。fitter を第 3 位置引数へ、mcmc_samples を fitter 構築の
#: プリセットへ変換する（いずれも add_* の kwarg ではないので ``consumes`` で取り除く）。
INVOKER = Invoker(frozenset({"fitter", "mcmc_samples"}), call_btlm)
