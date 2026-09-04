"""動作確認: 指標 1 つを実 lightweight-charts に重ねてスクリーンショットを保存する。

実行（ヘッドレス・1 指標 = 1 プロセス）:
    LANG=en_US.UTF-8 LC_ALL=en_US.UTF-8 \
    WEBKIT_DISABLE_COMPOSITING_MODE=1 WEBKIT_DISABLE_DMABUF_RENDERER=1 \
    LIBGL_ALWAYS_SOFTWARE=1 WEBKIT_DISABLE_SANDBOX_THIS_IS_DANGEROUS=1 \
    xvfb-run -a <venv-python> run_one.py <package_name>

`src` 名前空間がパッケージ間で衝突するため、必ず 1 プロセス 1 指標で起動する。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

_INDIGATORS = Path(__file__).resolve().parents[1]
_CSV = (
    _INDIGATORS.parent
    / "lightweight-charts-python-main/examples/4_line_indicators/ohlcv.csv"
)
_OUT = Path(__file__).resolve().parent / "out"

# package -> (adapter 関数名, 配置: "separate"=別ウィンドウ / "overlay"=メイン重畳 / "both")
_TARGETS: dict[str, tuple[str, str]] = {
    "profit_adx_needle": ("add_adx_needle", "separate"),
    "profit_arctan": ("add_arctan", "separate"),
    "profit_hl_band": ("add_hl_band", "overlay"),
    "profit_hlband": ("add_hlband_separate", "both"),
    "profit_mfi": ("add_mfi", "separate"),
    "profit_mfi_macd": ("add_mfimacd", "separate"),
    "profit_oscillator": ("add_oscillator", "separate"),
    "profit_oscillator2": ("add_oscillator2", "separate"),
    "profit_osi_ma": ("add_osi_ma", "separate"),
    "profit_rmm": ("add_rmm", "separate"),
    "profit_rmm_macd": ("add_rmmmacd", "separate"),
    "profit_rsi": ("add_rsi", "separate"),
    "profit_rsi_macd": ("add_rsimacd", "separate"),
    "profit_stc": ("add_stc", "separate"),
    "profit_volatility": ("add_volatility", "separate"),
}


def main() -> None:
    package = sys.argv[1]
    func_name, placement = _TARGETS[package]

    pkg_dir = _INDIGATORS / package
    sys.path.insert(0, str(pkg_dir))
    import importlib

    lwc_chart = importlib.import_module("src.lwc_chart")
    add_fn = getattr(lwc_chart, func_name)

    df = pd.read_csv(_CSV)

    from lightweight_charts import Chart

    chart = Chart(width=1000, height=700, title=package, inner_width=1, inner_height=0.6)
    chart.set(df)

    sub = None
    if placement in ("separate", "both"):
        sub = chart.create_subchart(position="below", width=1, height=0.4, sync=True)
        add_fn(sub, df)
    if placement == "overlay":
        add_fn(chart, df)
    if placement == "both":  # hlband は overlay 8 本も併載
        lwc_chart.add_hlband_overlay(chart, df)

    chart.show()
    _OUT.mkdir(exist_ok=True)
    out = _OUT / f"{package}_lwc.png"
    out.write_bytes(chart.screenshot())
    saved = [str(out)]
    if sub is not None:  # 指標ペイン（サブチャート）のキャンバスも取得する
        sub_out = _OUT / f"{package}_lwc_sub.png"
        sub_out.write_bytes(sub.screenshot())
        saved.append(str(sub_out))
    chart.exit()
    print(f"saved: {', '.join(saved)}")


if __name__ == "__main__":
    main()
