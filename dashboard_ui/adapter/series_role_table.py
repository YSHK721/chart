"""§3.1 / §3.2 の役割判定表（水準 / 非水準・行ラベル・第 2 表のセル宣言）。

判定の規約（設計書 §3.1）:
    水準でない系列の除外は**名前ではなく実値の桁**で行う。現在値の 0.3〜3 倍に入るものを
    価格スケールの水準とみなす。名前で除外すると、指標が系列を増やしたとき（あるいは
    設定で系列名が変わったとき）に取り残しが起き、`btlm_trail_beta` / `btlm_trail_sigma` /
    `btlm_trail_band_hit_rate` の 21 本を水準として数えた §11 の誤りが再発する。

§8 OCP: 「積み上がる量か」（§5.3.3）も**性質の宣言**で切り替える。呼び出し側（usecase）に
指標名の分岐を作らない。宣言に無い指標は第 2 表のセルを持たない（キーが無い＝構造で表れる）。

水準パラメータ（上側分位・窓の本数・イベント件数）の既定は**指標カタログ**が唯一源であり、
本モジュールは既定値を発明しない（設定に無ければカタログの値を読む）。
"""
from __future__ import annotations

import functools
import importlib
import math
import re
from dataclasses import dataclass
from typing import Callable, Mapping

import numpy as np

from dashboard_ui.adapter.gateway.material_store import MaterialStore
from dashboard_ui.domain.material_version import fingerprint_of
from dashboard_ui.usecase.sheet_models import OscillatorSpec, SeriesRole, SheetInstance

#: 価格スケールとみなす倍率の下限・上限（§3.1・境界を含む）。
_LEVEL_LOW: float = 0.3
_LEVEL_HIGH: float = 3.0

#: 行ラベルに出さないパラメータ（見た目だけを決めるもの）。
_COSMETIC_PARAMS: "frozenset[str]" = frozenset({"color"})

#: 表示 3 分割（依頼者指示 2026-08-30）の「期間」に採る主要 lookback パラメータの優先順。
#: 指標により期間パラメータの名前が違うため、ここが表示上の対応の唯一の宣言点。
#: 先に一致した 1 つだけを期間列に出す（複数持つ指標の残りは「その他」へ落ちる）。
_PERIOD_PARAMS: "tuple[str, ...]" = (
    "length", "maxbars", "n_har", "rsi_period", "window_n", "period",
)

#: 同じく「ソース」（計算に使う価格系列）に採るパラメータ名の優先順。
_SOURCE_PARAMS: "tuple[str, ...]" = ("source", "price", "src")

#: ラダーの**水準値を変えない**パラメータ（描画・凡例・付随メトリクス系列のみに効く）。
#: 行の読み手に伝える情報が無いので、表示 3 分割の `extra` から除く（依頼者指摘 2026-08-30
#: 「まったく伝わらない」）。カタログ（catalog.js）の宣言と各指標の実装で確認した集合:
#:   color/width/bull_color/bear_color … 線の見た目のみ
#:   legend/draw_levels/display_mode/dash_opacity … 描画の有無・形式のみ
#:   show_metrics（btlm_trail の β・実績率の表示）/ show_outliers（cvfe の外れ線の表示）
#:     … 系列を出すか出さないかのみで、出ている水準の値は不変
#:   n_cov … band_hit_rate（価格水準でないメトリクス）の窓のみ
#: σ倍率（sigma_inner/outer）・q_out・band_method 等は**水準の定義そのもの**なので除かない。
_LADDER_NOISE_PARAMS: "frozenset[str]" = frozenset({
    "color", "width", "bull_color", "bear_color",
    "legend", "draw_levels", "display_mode", "dash_opacity",
    "show_metrics", "show_outliers", "n_cov",
})


def _rsi_headroom_excess(value: float, band_high: float) -> float:
    """RSI の超過分（`(v - u) / (100 - u)`）。上限は指標側 `levels.RSI_MAX` が唯一源。"""
    return (float(value) - float(band_high)) / (_rsi_max() - float(band_high))


def _plain_excess(value: float, band_high: float) -> float:
    """生スケールの超過分（`v - u`）。有界でない量（tick 数・乖離率）はこちら。"""
    return float(value) - float(band_high)


@dataclass(frozen=True)
class OscillatorDeclaration:
    """第 2 表のセルを作るために指標ごとに宣言する性質（§3.2 の表の機械可読版）。"""

    value_series: str
    level_prefix: str
    cumulative: bool = False
    excess: Callable[[float, float], float] = _plain_excess


#: 指標 → 宣言。ここに無い指標は第 2 表のセルを持たない（除外の列挙を書かない）。
_OSCILLATORS: "Mapping[str, OscillatorDeclaration]" = {
    "ma_marod": OscillatorDeclaration("ma_marod", "ma_marod"),
    "btlm_trail_marod": OscillatorDeclaration("btlm_trail_marod", "btlm_trail_marod"),
    "profit_rsi": OscillatorDeclaration("rsi", "rsi", excess=_rsi_headroom_excess),
    # tick 数は足の中で積み上がる量である（§5.3.3: 部分和は同じ経過の過去へ当てる）。
    "tickvol": OscillatorDeclaration("tickvol", "tickvol", cumulative=True),
}


def _indicator_module(indicator_id: str, submodule: str):
    """指標 src の公開モジュールを read-only で解決する（写しを持たない）。

    探索パスの用意は `indigators.indicator_ui.api_loader` が唯一源
    （replay / sim と同形の read-only 再利用）。
    """
    from indigators.indicator_ui import api_loader  # 遅延: 技術隔離

    api_loader.load_compute()
    from adapter.compute.call_binding import indicator_src  # 遅延: 技術隔離を本層に閉じる

    src = indicator_src(indicator_id)
    return importlib.import_module(f"{src.__name__}.{submodule}")


@functools.lru_cache(maxsize=1)
def _rsi_max() -> float:
    """RSI の上限（指標 core の定数）。**プロセス寿命で 1 回だけ**解決する（ISSUE-464 ②）。

    値は指標 core の定数であり、epoch にも要求にも依らない。にもかかわらず超過分の定義が
    評価のたびにこれを引き直していた（実測 2026-08-30: 8 足束 1 要求で 31,788 回・410 ms）。
    1 回の解決は bridge の探索パス準備と `importlib.import_module` を通るため安くない。
    出力は正しいままなので状態検証では落ちない——ISSUE-450 / ISSUE-257 と同型である。

    唯一源は依然として指標 core の `levels.RSI_MAX` であり、値の写しはここに持たない。
    """
    return float(_indicator_module("profit_rsi", "levels").RSI_MAX)


def reset_core_constants() -> None:
    """プロセス寿命で保持している core 由来の定数を捨てる（検定の後始末のための面）。"""
    _rsi_max.cache_clear()


def _catalog_defaults() -> "Mapping[str, Mapping[str, object]]":
    """指標カタログの既定パラメータ（`GET /catalog` が配るものと同一の唯一源）。"""
    from indigators.indicator_ui import api_loader  # 遅延: 技術隔離

    api_loader.load_compute()
    from adapter.compute.catalog_schema import catalog_defaults  # 遅延: 技術隔離

    return catalog_defaults()


def _finite_values(values: np.ndarray) -> np.ndarray:
    """有限値だけを残す（O(n)）。**確定ぶんに対して epoch の中で不変**なので持ち越せる。"""
    return values[np.isfinite(values)]


class SeriesRoleTable:
    """役割宣言ポートの実装。判定表は本モジュールが所有する。

    Args:
        param_defaults: 水準パラメータの既定（省略時は指標カタログから引く）。
        store: 確定ぶんの中間結果を epoch 単位で持つストア（ISSUE-464 ④）。**省略時は
            この表だけのストア**になり、共有は自分の寿命で閉じる。要求をまたいで共有
            するかどうかは Composition Root の決定である（adapter は自分で相手を選ばない）。
    """

    def __init__(
        self, param_defaults: "Mapping[str, Mapping[str, object]] | None" = None,
        *, store: "MaterialStore | None" = None,
    ) -> None:
        self._defaults = param_defaults
        self._store = store if store is not None else MaterialStore()

    # ------------------------------------------------------------------ 役割
    def role_of(
        self, *, instance: SheetInstance, series_name: str,
        values: "tuple[float, ...]", reference_price: float,
    ) -> SeriesRole:
        """その系列が価格スケールの水準か否か。

        判定は 2 段:
        1. **性質による構造的除外**: 積み上がる量（cumulative 宣言・§5.3.3）は件数であり、
           価格になり得ない。週足のティック数（中央値 ~10 万）が実値の帯（0.3〜3 倍）へ
           偶然入り、tickvol が価格 488,103 円の行としてラダーに出た（ISSUE-462 で実発生。
           裁定 2026-08-29「tickvol はラダーに一切出さない」）。除外は名前の列挙ではなく
           既存の宣言（_OSCILLATORS.cumulative＝単一ソース）で行う（§11 の再発防止を維持）。
        2. 実値の桁（§3.1・従来どおり）。

        計算量（ISSUE-464 ④）: 系列のうち動くのは**形成中バーの 1 点だけ**であり、確定ぶんの
        有限値抽出は epoch の中で不変である。確定ぶんはストアへ持ち越し、末尾 1 点だけを
        毎要求継いで中央値を取る（§7 の 2 段を素材の側で構造にした ISSUE-457 と同じ規律）。
        中央値そのものは**全点**から取るので判定は従来と 1 ビットも変わらない。
        """
        declaration = _OSCILLATORS.get(instance.indicator_id)
        if declaration is not None and declaration.cumulative:
            return SeriesRole.NOT_LEVEL
        finite = self._finite_of(instance, series_name, values)
        if finite.size == 0:
            return SeriesRole.NOT_LEVEL
        median = float(np.median(finite))
        price = float(reference_price)
        inside = _LEVEL_LOW * price <= median <= _LEVEL_HIGH * price
        return SeriesRole.PRICE_LEVEL if inside else SeriesRole.NOT_LEVEL

    def _finite_of(
        self, instance: SheetInstance, series_name: str, values: "tuple[float, ...]"
    ) -> np.ndarray:
        """系列の有限値（確定ぶんは持ち越し、形成中の 1 点だけを毎回継ぐ）。

        並びは元の系列の順のままである（`np.median` は同じ入力に対して決定的なので、
        持ち越しても中央値はビット単位で一致する）。
        """
        confirmed = np.asarray(values[:-1], dtype=np.float64)
        shared = self._store.material(
            key=("role", instance.key, series_name),
            epoch=fingerprint_of(confirmed),
            name="finite",
            factory=lambda: _finite_values(confirmed),
        )
        if not values:
            return shared
        forming = np.float64(values[-1])
        return (
            np.append(shared, forming) if np.isfinite(forming) else shared
        )

    # ---------------------------------------------------------------- ラベル
    def row_label(self, *, instance: SheetInstance, series_name: str) -> str:
        """ラダー行のラベル（§11-2: パラメータまで含めて一意にする）。

        既定と異なるパラメータだけを `名前=値` で添える。同一足・同一指標で 2 本の instance が
        並ぶのは設定が違うときだけなので、違いを添えれば一意になり、既定どおりの行は短く出る。
        値だけを並べる形（旧実装）は `False` や `981` が何のパラメータか読者に復元できず、
        認知負荷の厳命と衝突した（依頼者承認 2026-08-30 で名前つきへ変更）。

        カタログに存在しないキーは出さない（依頼者承認 2026-08-30）。撤去済みパラメータ
        （例: `wait_for_close`・ISSUE-286 で撤去）が保存済みテンプレートに残骸として残ると、
        既定が引けず必ず「既定と異なる」と判定されて計算に使われない値がラベルへ漏れるため。
        """
        defaults = dict(self._param_defaults().get(instance.indicator_id) or {})
        marks = [
            f"{key}={value}"
            for key, value in sorted(instance.params.items())
            if key in defaults and key not in _COSMETIC_PARAMS and value != defaults[key]
        ]
        return series_name if not marks else f"{series_name} {' '.join(marks)}"

    def row_naming(self, *, instance: SheetInstance, series_name: str) -> "dict[str, object]":
        """行の表示 3 分割（依頼者指示 2026-08-30: 指標名 / 期間 / ソース）。

        `row_label`（§11-2 の一意な識別子）とは役割が違う: こちらは**読むための分解**で、
        期間・ソースは既定どおりでも常に出す（列になった以上、空欄は「無い」と読まれる）。
        期間・ソースに該当しない非既定パラメータだけを `extra` へ `名前=値` で残す。
        """
        defaults = dict(self._param_defaults().get(instance.indicator_id) or {})

        def effective(names: "tuple[str, ...]") -> "tuple[str | None, object | None]":
            for name in names:
                if name in instance.params:
                    return name, instance.params[name]
                if name in defaults:
                    return name, defaults[name]
            return None, None

        period_key, period = effective(_PERIOD_PARAMS)
        source_key, source = effective(_SOURCE_PARAMS)
        consumed = {period_key, source_key} | _LADDER_NOISE_PARAMS | set(_COSMETIC_PARAMS)
        extra = " ".join(
            f"{key}={value}"
            for key, value in sorted(instance.params.items())
            if key in defaults and key not in consumed and value != defaults[key]
        )
        # 水準部の分離（依頼者指示 2026-08-30: q95 等も列へ分割）。系列名は
        # `<indicator_id>_<水準>`（例 btlm_trail_q95・cvfe_u1）の規約なので、接頭辞を
        # 剥がした残りを水準とする。規約に乗らない系列（例 MA）は名前のみ・水準は空。
        prefix = f"{instance.indicator_id}_"
        if series_name.startswith(prefix):
            name, level = instance.indicator_id, series_name[len(prefix):]
        else:
            name, level = series_name, ""
        level_p, level_note = self._level_p(level, effective)
        return {
            "name": name,
            "level": self._level_display(level, effective),
            "level_p": level_p,
            "level_note": level_note,
            "period": period,
            "source": None if source is None else str(source),
            "extra": extra,
        }

    @staticmethod
    def _level_p(level: str, effective) -> "tuple[float | None, str | None]":
        """水準の**宣言分位**（依頼者裁定 2026-08-30: 水準セルは宣言された極端度で塗る）。

        実測の位置は価格セルの horizon_p（§5.5.5）が担う。ここは「この線がどれだけ端で
        あることを主張して引かれたか」の宣言のみを読む（1 冊に読み方を 2 つ作らない）:
          q{pct}      → pct/100（定義そのもの）
          off_hi/lo   → 実効 q_out / 1−q_out（パラメータが宣言する分位そのもの・仮定ゼロ）
          mean / mid  → 0.5（中心の宣言。p=0.5 は透明＝色は付かない）
          ±kσ 帯      → Φ(±k)（σ という宣言の正規換算。唯一の仮定なので注記を添える）
        宣言を持たない水準は (None, None)＝色を置かない。
        """
        m = re.search(r"(?:^|_)q(\d{1,3})$", level)
        if m is not None:
            pct = int(m.group(1))
            return (pct / 100.0, None) if 0 <= pct <= 100 else (None, None)
        if level in {"mean", "mid"}:
            return 0.5, None

        def q_out_p(upper: bool) -> "tuple[float | None, str | None]":
            _key, value = effective(("q_out",))
            if value is None:
                return None, None
            q = float(value)
            return (q if upper else 1.0 - q), None

        def sigma_p(name: str, upper: bool) -> "tuple[float | None, str | None]":
            _key, value = effective((name,))
            if value is None:
                return None, None
            k = float(value)
            phi = 0.5 * (1.0 + math.erf(k / math.sqrt(2.0)))
            return (phi if upper else 1.0 - phi), f"正規換算 Φ({k:g}σ)"

        table = {
            "off_hi": lambda: q_out_p(upper=True),
            "off_lo": lambda: q_out_p(upper=False),
            "u1": lambda: sigma_p("sigma_inner", upper=True),
            "l1": lambda: sigma_p("sigma_inner", upper=False),
            "u2": lambda: sigma_p("sigma_outer", upper=True),
            "l2": lambda: sigma_p("sigma_outer", upper=False),
        }
        entry = table.get(level)
        return (None, None) if entry is None else entry()

    @staticmethod
    def _level_display(level: str, effective) -> str:
        """水準トークンの日本語表記（依頼者指示 2026-08-30: u1→内側上 1σ の形）。

        語は依頼者所有の版面モック（アーティファクト 1707bef3）の表記に従う:
        内側上/内側下・外側上/外側下（σ 倍率つき）・外れ上/外れ下・中心。
        σ の数字は当該 instance の実効値（sigma_inner / sigma_outer）で、既定を書き写さない。
        対応の無いトークン（q95・mean 等・モックも原語のまま）は変換しない。
        """
        def sigma(name: str) -> str:
            _key, value = effective((name,))
            if value is None:
                return "?"
            text = f"{float(value):g}"
            return text
        table = {
            "u1": lambda: f"内側上 {sigma('sigma_inner')}σ",
            "l1": lambda: f"内側下 {sigma('sigma_inner')}σ",
            "u2": lambda: f"外側上 {sigma('sigma_outer')}σ",
            "l2": lambda: f"外側下 {sigma('sigma_outer')}σ",
            "off_hi": lambda: "外れ上",
            "off_lo": lambda: "外れ下",
            "mid": lambda: "中心",
        }
        entry = table.get(level)
        return level if entry is None else entry()

    # ------------------------------------------------------------ セルの宣言
    def oscillator_spec(
        self, *, instance: SheetInstance, series_names: "frozenset[str]"
    ) -> "OscillatorSpec | None":
        """第 2 表のセル宣言（オシレータでなければ None）。"""
        declaration = _OSCILLATORS.get(instance.indicator_id)
        if declaration is None:
            return None
        settings = _Settings(
            indicator_id=instance.indicator_id,
            params=dict(instance.params),
            defaults=dict(self._param_defaults().get(instance.indicator_id) or {}),
        )
        q_high = float(settings.value("q_high"))
        # 下帯（q_low）は設定にもカタログにも無ければ持たない（既定を発明しない）。
        try:
            q_low = float(settings.value("q_low"))
        except KeyError:
            q_low = None
        return OscillatorSpec(
            value_series=declaration.value_series,
            band_high_series=f"{declaration.level_prefix}_q{_percent(q_high)}",
            q_high=q_high,
            band_low_series=(
                None if q_low is None
                else f"{declaration.level_prefix}_q{_percent(q_low)}"
            ),
            q_low=q_low,
            window_n=int(settings.value("window_n")),
            k_events=int(settings.value("k_events")),
            cumulative=declaration.cumulative,
            excess=declaration.excess,
        )

    # ------------------------------------------------------------ パラメータ台帳
    def known_params(self, *, indicator_id: str) -> "frozenset[str] | None":
        """カタログが定義するパラメータ名（指標がカタログに無ければ None＝判定しない）。"""
        defaults = self._param_defaults().get(indicator_id)
        return None if defaults is None else frozenset(defaults)

    # ------------------------------------------------------------------ 内部
    def _param_defaults(self) -> "Mapping[str, Mapping[str, object]]":
        if self._defaults is None:
            self._defaults = _catalog_defaults()
        return self._defaults


@dataclass(frozen=True)
class _Settings:
    """設定 → カタログ既定 の順で水準パラメータを引く（既定を発明しない）。"""

    indicator_id: str
    params: "Mapping[str, object]"
    defaults: "Mapping[str, object]"

    def value(self, name: str) -> object:
        """`name` の値。設定にもカタログにも無ければ KeyError（無言の縮退を作らない）。"""
        if name in self.params:
            return self.params[name]
        if name in self.defaults:
            return self.defaults[name]
        raise KeyError(
            f"水準パラメータ {name!r} が設定にもカタログ既定にもありません"
            f"（indicatorId={self.indicator_id!r}）"
        )


def _percent(quantile: float) -> int:
    """分位を系列名の百分率へ（§7.1.1 の `_q{q_hi}` 展開と同一規約）。"""
    return int(round(float(quantile) * 100.0))
