"""tgp BTLM を lightweight-charts に重ねるデモ（matplotlib 版 demo.py と並列）。

2 つの出力を生成する:
  * StaticLWC による自己完結 HTML（ディスプレイ不要）
  * デスクトップ Chart のスクリーンショット PNG（Xvfb 等の表示環境が必要）

既定は R 不要の `OlsBtlmFitter`（参照実装）。忠実版は `TgpBtlmFitter`（要 R + tgp + rpy2）。

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
from src import OlsBtlmFitter  # noqa: E402
from src.lwc_chart import add_btlm  # noqa: E402

# 既定の入力 CSV（lightweight-charts 同梱サンプル: date/open/high/low/close/volume）
_DEFAULT_CSV = (
    Path(__file__).resolve().parents[2]
    / "lightweight-charts-python-main/examples/4_line_indicators/ohlcv.csv"
)
_OUT_DIR = Path(__file__).resolve().parent / "out"
_MAXBARS = 250


def make_html(df: pd.DataFrame, out: Path) -> Path:
    """StaticLWC で自己完結 HTML を生成する（ディスプレイ不要）。"""
    from lightweight_charts.widgets import StaticLWC

    chart = StaticLWC(900, 600)
    chart.legend(visible=True)
    chart.set(df)
    add_btlm(chart, df, OlsBtlmFitter(), maxbars=_MAXBARS)
    chart.load()
    html = chart._html + "</script></body></html>"
    out.write_text(html)
    return out


def make_screenshot(df: pd.DataFrame, out: Path) -> Path:
    """デスクトップ Chart を起動し、描画結果を PNG で保存する（表示環境が必要）。"""
    from lightweight_charts import Chart

    chart = Chart(width=900, height=600, title="tgp BTLM")
    chart.legend(visible=True)
    chart.set(df)
    add_btlm(chart, df, OlsBtlmFitter(), maxbars=_MAXBARS)
    chart.show()
    png = chart.screenshot()
    chart.exit()
    out.write_bytes(png)
    return out


def main() -> None:
    csv = Path(sys.argv[1]) if len(sys.argv) > 1 else _DEFAULT_CSV
    _OUT_DIR.mkdir(exist_ok=True)
    # 回帰チャネルが画面全体に見えるよう、当てはめ窓と同じ長さに揃える。
    df = pd.read_csv(csv).tail(_MAXBARS).reset_index(drop=True)

    html = make_html(df, _OUT_DIR / "btlm_lwc.html")
    print(f"HTML  OK: {html} ({html.stat().st_size} bytes)")

    try:
        png = make_screenshot(df, _OUT_DIR / "btlm_lwc.png")
        print(f"PNG   OK: {png} ({png.stat().st_size} bytes)")
    except Exception as e:  # 表示環境が無い場合は HTML のみ
        print(f"PNG SKIPPED (no display backend?): {type(e).__name__}: {e}")


if __name__ == "__main__":
    main()
