"""profit_band を lightweight-charts に重ねるデモ。

2 つの出力を生成する:
  * StaticLWC による自己完結 HTML（ディスプレイ不要）
  * デスクトップ Chart のスクリーンショット PNG（Xvfb 等の表示環境が必要）

実行（ヘッドレス例）:
    LANG=en_US.UTF-8 LC_ALL=en_US.UTF-8 \
    WEBKIT_DISABLE_COMPOSITING_MODE=1 WEBKIT_DISABLE_DMABUF_RENDERER=1 \
    LIBGL_ALWAYS_SOFTWARE=1 WEBKIT_DISABLE_SANDBOX_THIS_IS_DANGEROUS=1 \
    xvfb-run -a python lwc_demo.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from src.lwc_chart import add_profit_band  # noqa: E402

# 既定の入力 CSV（lightweight-charts の同梱サンプル: date/open/high/low/close/volume）
_DEFAULT_CSV = (
    Path(__file__).resolve().parents[2]
    / "lightweight-charts-python-main/examples/4_line_indicators/ohlcv.csv"
)
_OUT_DIR = Path(__file__).resolve().parent / "out"


def _load(csv: Path) -> pd.DataFrame:
    return pd.read_csv(csv)


def make_html(df: pd.DataFrame, out: Path) -> Path:
    """StaticLWC で自己完結 HTML を生成する（ディスプレイ不要）。"""
    from lightweight_charts.widgets import StaticLWC

    chart = StaticLWC(900, 600)
    chart.set(df)
    add_profit_band(chart, df)
    chart.load()
    html = chart._html + "</script></body></html>"
    out.write_text(html)
    return out


def make_screenshot(df: pd.DataFrame, out: Path) -> Path:
    """デスクトップ Chart を起動し、描画結果を PNG で保存する（表示環境が必要）。"""
    from lightweight_charts import Chart

    chart = Chart(width=900, height=600, title="PRO!fit_Band")
    chart.set(df)
    add_profit_band(chart, df)
    chart.show()
    png = chart.screenshot()
    chart.exit()
    out.write_bytes(png)
    return out


def main() -> None:
    csv = Path(sys.argv[1]) if len(sys.argv) > 1 else _DEFAULT_CSV
    _OUT_DIR.mkdir(exist_ok=True)
    df = _load(csv)

    html = make_html(df, _OUT_DIR / "profit_band_lwc.html")
    print(f"HTML  OK: {html} ({html.stat().st_size} bytes)")

    try:
        png = make_screenshot(df, _OUT_DIR / "profit_band_lwc.png")
        print(f"PNG   OK: {png} ({png.stat().st_size} bytes)")
    except Exception as e:  # 表示環境が無い場合は HTML のみ
        print(f"PNG SKIPPED (no display backend?): {type(e).__name__}: {e}")


if __name__ == "__main__":
    main()
