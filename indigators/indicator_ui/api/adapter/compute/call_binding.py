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
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any, Callable, TypedDict

from adapter.compute.module_loader import load_package

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


def _fitter_factory(name: str) -> Any:
    """fitter enum 文字列 → Fitter 実体（§3.3.3 fitter_factory）。

    "ols" → OlsBtlmFitter()、"tgp" → TgpBtlmFitter()（tgp_btlm/src/__init__.py:38-39）。
    rpy2/R 不在でも TgpBtlmFitter の実体化自体は成功し、fit_predict 時に ImportError。
    """
    src = _load_src_package("tgp_btlm")
    if name == "ols":
        return src.OlsBtlmFitter()
    if name == "tgp":
        return src.TgpBtlmFitter()
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
            fitter = _fitter_factory(kw.pop("fitter"))
            callable_(chart, df, fitter, **kw)
        else:
            callable_(chart, df, **params)
