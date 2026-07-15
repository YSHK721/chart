"""CALL_BINDING（内部設計書 §3.3.3・基本設計 §5.5.4.1）— 指標ごとの呼出規約。

compute_id(+variant) → {callable, output_kind, keyword_params} を保持し、``invoke`` で
既存 add_* を一意・決定論的に呼ぶ。add_btlm のみ fitter を第3位置引数で渡し、他は
df 以降キーワード専用（§5.5.4.1）。fitter enum 文字列 → Fitter 実体化
（ols→OlsBtlmFitter() / tgp→TgpBtlmFitter()）をここで行う。

3 指標はいずれも top-level パッケージ名 ``src`` を使うため、``import src`` では同名衝突し
1 つしか読めない。本モジュールは各指標 src を **ファイルパスから一意なパッケージ名で
読み込む**（既存 src は read-only・改変しない）。描画ライブラリは import しない。
"""

from __future__ import annotations

import importlib
import inspect
import sys
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any, Callable, TypedDict

from adapter.compute.module_loader import load_package

# 共有プリミティブ層（``common.applied_price`` 等）を指標 src から絶対 import 可能にする。
#   指標 src（例 moving_averages/src/lwc_chart.py）が ``from common.applied_price import ...``
#   を解決できるよう、ワークスペース根（このファイル: api/adapter/compute/ → parents[5]）を
#   sys.path に追加する（ロード境界で一括設定し、各 src に sys.path ハックを散らさない）。
# ISSUE-087 🟡-3: repo 根/MP api の解決は venv の .pth（tools/install_dev_paths.py）が担う（実行時 sys.path 改変を撤去）。


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

# indigators/ ルート（このファイル: api/adapter/compute/ → parents[4] = indigators/）。
_INDIGATORS = Path(__file__).resolve().parents[4]

# 一意パッケージ名の接頭辞。3 指標が共通 top-level 名 ``src`` を使うため、各指標を
# ``_<indicator>_src`` という衝突しない名前で sys.modules へ登録する（同名 src 回避）。
_SRC_MODULE_PREFIX = "_"
_SRC_MODULE_SUFFIX = "_src"


def _src_module_name(indicator: str) -> str:
    """指標名から一意なパッケージ名（``_<indicator>_src``）を組み立てる。"""
    return f"{_SRC_MODULE_PREFIX}{indicator}{_SRC_MODULE_SUFFIX}"


def _load_src_package(indicator: str) -> ModuleType:
    """指標 src パッケージを一意なパッケージ名で読み込む（同名 ``src`` 衝突を回避）。

    importlib 機構は ``module_loader.load_package`` に集約（重複解消・振る舞い不変）。
    一意名 ``_<indicator>_src`` を与え、相対 import（``from .bands import``）と
    sys.modules キャッシュは load_package が担保する。
    """
    return load_package(_src_module_name(indicator), _INDIGATORS / indicator / "src")


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
# 既定サンプル。catalog.js の mcmc_samples 既定（'standard'）と**一致必須**（不一致だと
# UI 既定と API 直叩き既定が乖離する）。test_fitter_factory_default_matches_catalog で固定。
_DEFAULT_SAMPLES = "standard"


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


def _load_callable(indicator: str, attr: str) -> Callable:
    """指標 src の lwc_chart から add_* を取り出す（read-only）。"""
    src = _load_src_package(indicator)
    lwc = importlib.import_module(src.__name__ + ".lwc_chart")
    return getattr(lwc, attr)


class _BindingSpec(TypedDict):
    """_TABLE のエントリ形状（compute_id+variant ごとの呼出規約）。

    loader     : add_* を遅延ロードする callable（指標 src 同名衝突を回避するため遅延）。
    output_kind: 系列 JSON 種別（"line" / "horizontal_line"・§6.3）。
    kind       : invoke 時の引数渡し（"btlm"=fitter 第3位置 / "kw"=df 以降キーワード専用）。
    """

    loader: Callable[[], Callable]
    output_kind: str
    kind: str


# compute_id(+variant) → 規約。loader は import を遅延し、指標 src 同名衝突を回避する。
_TABLE: dict[tuple[str, str], _BindingSpec] = {
    ("tgp_btlm", "default"): {
        "loader": lambda: _load_callable("tgp_btlm", "add_btlm"),
        "output_kind": "line", "kind": "btlm",
    },
    ("profit_band", "global"): {
        "loader": lambda: _load_callable("profit_band", "add_profit_band"),
        "output_kind": "line", "kind": "kw",
    },
    ("profit_band", "robust"): {
        "loader": lambda: _load_callable("profit_band", "add_robust_profit_band"),
        "output_kind": "line", "kind": "kw",
    },
    ("price_range_power", "default"): {
        "loader": lambda: _load_callable("price_range_power", "add_price_range_power"),
        "output_kind": "horizontal_line", "kind": "kw",
    },
    ("moving_averages", "default"): {
        "loader": lambda: _load_callable("moving_averages", "add_moving_averages"),
        "output_kind": "line", "kind": "kw",
    },
    # --- profit_* 系（MQL 移植・lwc 仕様）。統合 FakeChart が line/histogram/水平線を
    #     一括収集するため output_kind は分岐に不使用（resolve 互換のため残置）。kind は全て kw。---
    ("profit_adx_needle", "default"): {
        "loader": lambda: _load_callable("profit_adx_needle", "add_adx_needle"),
        "output_kind": "histogram", "kind": "kw",
    },
    ("profit_arctan", "default"): {
        "loader": lambda: _load_callable("profit_arctan", "add_arctan"),
        "output_kind": "histogram", "kind": "kw",
    },
    ("profit_hl_band", "default"): {
        "loader": lambda: _load_callable("profit_hl_band", "add_hl_band"),
        "output_kind": "horizontal_line", "kind": "kw",
    },
    ("profit_hlband", "separate"): {
        "loader": lambda: _load_callable("profit_hlband", "add_hlband_separate"),
        "output_kind": "histogram", "kind": "kw",
    },
    ("profit_hlband", "overlay"): {
        "loader": lambda: _load_callable("profit_hlband", "add_hlband_overlay"),
        "output_kind": "horizontal_line", "kind": "kw",
    },
    ("profit_mfi", "default"): {
        "loader": lambda: _load_callable("profit_mfi", "add_mfi"),
        "output_kind": "line", "kind": "kw",
    },
    ("profit_mfi_macd", "default"): {
        "loader": lambda: _load_callable("profit_mfi_macd", "add_mfimacd"),
        "output_kind": "histogram", "kind": "kw",
    },
    ("profit_oscillator", "default"): {
        "loader": lambda: _load_callable("profit_oscillator", "add_oscillator"),
        "output_kind": "histogram", "kind": "kw",
    },
    ("profit_oscillator2", "default"): {
        "loader": lambda: _load_callable("profit_oscillator2", "add_oscillator2"),
        "output_kind": "histogram", "kind": "kw",
    },
    ("profit_osi_ma", "default"): {
        "loader": lambda: _load_callable("profit_osi_ma", "add_osi_ma"),
        "output_kind": "histogram", "kind": "kw",
    },
    ("profit_rmm", "default"): {
        "loader": lambda: _load_callable("profit_rmm", "add_rmm"),
        "output_kind": "histogram", "kind": "kw",
    },
    ("profit_rmm_macd", "default"): {
        "loader": lambda: _load_callable("profit_rmm_macd", "add_rmmmacd"),
        "output_kind": "histogram", "kind": "kw",
    },
    ("profit_rsi", "default"): {
        "loader": lambda: _load_callable("profit_rsi", "add_rsi"),
        "output_kind": "line", "kind": "kw",
    },
    ("profit_rsi_macd", "default"): {
        "loader": lambda: _load_callable("profit_rsi_macd", "add_rsimacd"),
        "output_kind": "histogram", "kind": "kw",
    },
    ("profit_stc", "default"): {
        "loader": lambda: _load_callable("profit_stc", "add_stc"),
        "output_kind": "line", "kind": "kw",
    },
    ("profit_volatility", "default"): {
        "loader": lambda: _load_callable("profit_volatility", "add_volatility"),
        "output_kind": "line", "kind": "kw",
    },
}


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
            callable_(chart, df, fitter, **_accepted_kwargs(callable_, kw))
        else:
            kw = _accepted_kwargs(callable_, params)
            if self.compute_id == "price_range_power" and "interval" in kw:
                # 高価格帯でのバンド爆発（ハング）を防ぐ自動適応（catalog/src は不変）。
                kw["interval"] = _adapt_prp_interval(df, kw)
            callable_(chart, df, **kw)
