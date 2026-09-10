"""実行トレース（ティック粒度の記録）の adapter 群（RUN_TRACE_BASIC_DESIGN §3・§6）。

**再輸出しない**（§6.5.3）。永続化段（`simulator/adapter/trace/parquet_trace_store.py`）を
ここへ再輸出すると、`simulator/adapter/trace/trace_window.py` を import しただけで
pyarrow / pandas が読まれ、D-5 の隔離（記録 OFF の run が技術ドライバの
import に巻き込まれない）が黙って壊れる。各モジュールを名指しで import すること。
先例は `adapter/repository/__init__.py` / `adapter/indicator/__init__.py`（いずれも docstring
のみ）である。
"""
