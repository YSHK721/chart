"""lwc_adapter — lightweight-charts 出力アダプタの共有プリミティブ（指標横断の単一実装）。

各指標パッケージの ``src/lwc_chart.py`` は「lightweight_charts を import せず duck typing で
chart を受ける」出力アダプタである（PORTING_GUIDE §2/§6）。その中で

    - :func:`resolve_times` … 時刻系列の解決順序（PORTING_GUIDE §5）
    - :func:`emit_line`     … 折れ線 1 本の生成 + NaN 除外 + ``set``
    - :func:`quantile_series_name` … 分位バンド端の系列名の綴り（``{prefix}_q{pct}``）
    - :class:`SeriesLike`   … 系列オブジェクトの構造的契約（``set`` のみ）

の 3 つは全パッケージで同一の規約であり、ISSUE-179（横断コピペ重複）時点で
``_resolve_times`` は 21 箇所、``_emit_line`` は 2 箇所、系列 Protocol（``_Line`` /
``_Histogram`` / ``_Series``）は 20 箇所へ複製されていた。規約の変更が最大 21 ファイルの
同時改変を要求する状態（拡張ではなく改変＝OCP 違反）を解消するため、ここへ 1 本化する。

配置（``common`` ではなく ``common_view``）:
    ``common`` は「純粋な価格計算（numpy のみ依存）」を担う層であり（``common/__init__.py``・
    ``common/marod_bands.py`` が明示）、pandas 依存の描画アダプタ規約を置くと当該層の依存契約が
    壊れる。本モジュールは表示仕様層 ``common_view``（``level_colors`` / ``LEVEL_LINE_WIDTH``）と
    同じアクター（チャート表示仕様）に属するためこちらへ置く。モジュール様式（単体ファイル・
    ``__init__`` へは再エクスポートしない・出自と依存を docstring に明記）は
    ``common/marod_bands.py`` / ``common/module_loader.py`` の慣行に合わせる。

出自と挙動不変:
    実体は ``profit_arctan/src/lwc_chart.py`` ほか 12 パッケージが持っていた ``_resolve_times``
    （``str(c).lower()`` 版・戻り値 ``pd.Series``）と、``btlm_trail_marod`` / ``ma_marod`` の
    ``_emit_line`` を **無改変で** 移設したもの。移設に伴う数値・例外挙動の変更は無い。

    ISSUE-179 時点では「c.lower() 版 7 パッケージ / list を返す moving_averages / profit_band」を
    挙動差ゆえ未統合として残していたが、ISSUE-502 段階 2（D-7）で実測し直した結果、指標
    パッケージ側の自前実装は **3 件**（profit_hl_band / profit_hlband / moving_averages）まで
    減っていた（c.lower() 版の多くは marketdata.time_column へ移設済み）。この 3 件は次の実測に
    より本モジュールへ統合した:

        - profit_hl_band / profit_hlband: 公開入口（add_hl_band / add_hlband_separate）では
          core（hl_band.py / hlband.py）が先に c.lower() で列照合するため、非 str 列名は時刻解決へ
          到達しない＝挙動差は非到達。
        - moving_averages: 唯一の利用点（系列 emit）は位置参照 times[j] のみで、index を 0..n-1 へ
          振り直した Series からも同一の Timestamp が得られる。

    （profit_band は ISSUE-179 以後に別途統合済み。同ファイル冒頭に挙動不変の実測記録あり。）
    これで指標パッケージ側の自前実装は **0 件**になった。

    同一規則の第 2 の所有者だった marketdata.time_column.resolve_times（c.lower() 版）は
    ISSUE-502 D-7 後続（2026-09-07）で撤去し、所有者は本モジュール **1 件**へ収束した。
    利用していた 5 パッケージ（tgp_btlm / profit_mfi / profit_osi_ma / profit_stc /
    profit_adx_needle）はいずれも SeriesLike のため本モジュールを既に import しており、
    束縛先を移すだけでパッケージ間の依存辺は 1 本も増えていない。

    向きの根拠（なぜ marketdata 側を撤去したか）: 当該規則の利用者は 5 件すべてが
    indigators/*/src/lwc_chart.py ＝チャート表示アクターであり、市場データの語彙に属していない
    （SRP）。逆向き（marketdata が本モジュールへ委譲する）は、common に対して機械的に
    禁じられている表示層依存と同型の安定度逆転になる
    （common/tests/test_package_surface_purity.py・ISSUE-104）。

    挙動不変の実測: 撤去した実装との差は c.lower() と str(c).lower() の 1 箇所のみ（AST 差分）。
    観測できる差は非 str 列名時の例外型（AttributeError → KeyError）だけで、全 5 入口で非到達
    （marketdata/ohlc_csv_loader.py の既定 cast_column_names=False が上流で先に AttributeError を
    投げる。marketdata/tests/test_csv_loader_policy.py が固定）。

    所有者 1 件・指標層 0 件は common_view/tests/test_resolve_times_single_rule.py が
    機械的に固定する。

依存: numpy / pandas のみ（指標パッケージ・描画ライブラリへは依存しない）。
"""

from __future__ import annotations

from typing import Optional, Protocol, runtime_checkable

import numpy as np
import pandas as pd


@runtime_checkable
class SeriesLike(Protocol):
    """chart が返す系列オブジェクトの構造的契約（``set`` のみを要求する）。

    各 ``lwc_chart.py`` の ``_Line`` / ``_Histogram`` / ``_Series`` の共通部。要求メソッドが
    異なる ``_Chart`` 側（``create_line`` のみ / ``horizontal_line`` を要求 等）は各パッケージ
    固有のままとし、ここへは潰さない。
    """

    def set(self, data: pd.DataFrame) -> None: ...


@runtime_checkable
class LineChartLike(Protocol):
    """:func:`emit_line` が要求する chart の構造的契約（``create_line`` のみ）。"""

    def create_line(self, name: str, **kwargs) -> SeriesLike: ...


def resolve_times(df: pd.DataFrame, time_column: Optional[str]) -> pd.Series:
    """時刻系列を解決する（明示指定 > time 列 > date 列 > DatetimeIndex の順）。

    Args:
        df: 対象 DataFrame。
        time_column: 時刻列の明示指定（大小不問）。None なら探索する。

    Returns:
        index を 0..n-1 へ reset した datetime の ``pd.Series``。

    Raises:
        KeyError: 指定の時刻列が無い、または time/date/DatetimeIndex が解決できない場合。
    """
    lower_map = {str(c).lower(): c for c in df.columns}
    if time_column is not None:
        tcol = lower_map.get(time_column.lower(), time_column)
        if tcol not in df.columns:
            raise KeyError(f"指定された時刻列が存在しません: {time_column}")
        return pd.to_datetime(df[tcol]).reset_index(drop=True)
    if "time" in lower_map:
        return pd.to_datetime(df[lower_map["time"]]).reset_index(drop=True)
    if "date" in lower_map:
        return pd.to_datetime(df[lower_map["date"]]).reset_index(drop=True)
    if isinstance(df.index, pd.DatetimeIndex):
        return pd.Series(df.index, name="time").reset_index(drop=True)
    raise KeyError("時刻を解決できません（time/date 列、または DatetimeIndex が必要）。")


def emit_line(
    chart: LineChartLike,
    name: str,
    times: pd.Series,
    values,
    color: str,
    style: str,
) -> object:
    """chart に折れ線 1 本を追加し、NaN 行を除外した系列を ``set`` する。

    値列名は系列名と完全一致させる（PORTING_GUIDE §5）。

    Returns:
        生成した系列オブジェクト。
    """
    line = chart.create_line(
        name=name, color=color, style=style, width=1, price_line=False, price_label=False
    )
    series = pd.DataFrame({"time": times, name: np.asarray(values, dtype=float)}).dropna()
    line.set(series)
    return line


def quantile_series_name(prefix: str, q: float) -> str:
    """分位 ``q``（0..1）に対応するバンド端の系列名を綴る（例 ``("btlm_trail", 0.05)`` -> ``btlm_trail_q5``）。

    ISSUE-502 段階 2（D-12）: btlm_trail / btlm_trail_marod / ma_marod / tickvol の 4 パッケージが
    f"{prefix}_q{int(round(q * 100))}" を逐語複製しており、prefix だけが違った。綴りの規則
    （百分率の整数へ丸めて接尾辞を付ける）は 1 箇所が所有し、prefix は引数で受ける。

    Args:
        prefix: 指標の系列名 prefix（呼び出し側の指標が自分の系列名を渡す）。
        q: 分位（0..1）。

    Returns:
        ``f"{prefix}_q{int(round(q * 100))}"``。

    Note:
        本規則は百分率の整数へ丸めるため一般には非単射である（同じ 1% 幅に入る 2 つの分位は
        同名になる。例: q=0.0 と q=0.005 はともに接尾辞 q0）。ただし ISSUE-502 段階 2 で実測した
        ところ、実使用分位（0.005 / 0.01 / 0.05 / 0.1 / 0.5 / 0.9 / 0.95 / 0.99 / 0.995）に衝突は
        **無い**（q=0.995 は 99.5 の偶数丸めで q100、q=0.99 は q99 で別名）。台帳
        .doc/solid_audit_20260906.md の「q=0.995/0.99 の同名衝突」は誤りであり、実測で棄却した。
        丸め挙動は移設元 4 実装と 1 文字も変えていない（挙動不変の移設）。
    """
    return f"{prefix}_q{int(round(q * 100))}"


__all__ = [
    "SeriesLike",
    "LineChartLike",
    "resolve_times",
    "emit_line",
    "quantile_series_name",
]
