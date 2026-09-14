#!/usr/bin/env python3
"""serve.py — prototype_260626-01 サーバ起動ランチャ。

使い方:  python3 prototype_260626-01/serve.py [PORT]   （既定 8796）

proto_server.py（本番フロント配信＋/candles・/compute＋untilTime）を適切な環境で起動する:
  - MARKETDATA_DATA_DIR を自動設定（既存データの読み取り基点）。
  - tgp_btlm の MCMC(fitter=tgp) には rpy2 が要る。現在の python に rpy2 が無ければ、
    rpy2 入り venv で自分自身を再実行する（あれば）。venv が無ければそのまま起動し、
    ols/profit_band/price_range_power（純Python）は動作・tgp(MCMC)選択時のみ計算エラーになる。
既存データ・本番コードは読み取り専用（このディレクトリのコピーのみを配信）。
"""
import os
import sys
import runpy

HERE = os.path.dirname(os.path.abspath(__file__))
os.environ.setdefault("MARKETDATA_DATA_DIR", "/workspaces/app/data/marketdata")

# rpy2 が現在の interpreter に無ければ rpy2 入り venv で再実行（TGP(MCMC) 計算のため）。
#   venv/bin/python は base python への symlink なので realpath 比較は使えない。再実行済みを
#   env フラグ（_SERVE_REEXEC）で判定し、無限ループを防ぐ。
try:
    import rpy2  # noqa: F401
except Exception:
    _VENV_PY = "/tmp/proto-rv/_scratch/venv/bin/python"
    if os.path.exists(_VENV_PY) and not os.environ.get("_SERVE_REEXEC"):
        os.environ["_SERVE_REEXEC"] = "1"
        os.execv(_VENV_PY, [_VENV_PY, os.path.join(HERE, "serve.py")] + sys.argv[1:])
    else:
        sys.stderr.write("[serve] 注意: rpy2 が無いため tgp_btlm の fitter=tgp(MCMC) は計算エラーになります"
                         "（ols/profit_band/price_range_power は動作）。\n")

# proto_server.py を __main__ として実行（argv[1]=PORT を素通し・既定 8796）。
sys.argv = [os.path.join(HERE, "proto_server.py")] + (sys.argv[1:] if len(sys.argv) > 1 else ["8796"])
runpy.run_path(os.path.join(HERE, "proto_server.py"), run_name="__main__")
