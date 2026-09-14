"""profit_band の呼出規約フック（共有 param 既定値・専用例外型ローダ）。

本モジュールは call_binding から分離した協働子である（ISSUE-502 段階 4B・SRP/OCP）。
call_binding は ``_TABLE`` の 2 エントリ（global / robust）から本モジュールを参照するだけで、
profit_band が「必須バケット空」を専用型で送出することも、2 variant が共有する param の
既定値も知らない。
"""

from __future__ import annotations

from typing import Any

from adapter.compute.src_packages import load_src_package

#: profit_band の 2 variant（global/robust）が **どちらも受理する** param の既定値。
#:   宣言粒度は variant（ISSUE-278 #8）だが、共有 param の既定値は 1 箇所に置き両エントリが
#:   参照する（同じリテラルを 2 度書かない＝値の食い違いを構造的に作らない）。
SHARED_PARAMS_DEFAULTS: dict[str, Any] = {
    "probabilities": [0.51, 0.8, 0.85, 0.9, 0.95, 0.98, 0.99],
    "buckets": ["nOH", "pOL", "pOH", "nOL"],
    "legend": False,
}


def empty_bucket_error() -> type:
    """profit_band src の ``EmptyBucketError`` 型を返す（LSP 是正・型識別用）。

    profit_band src を一意パッケージ名で遅延ロードし専用例外型を返す。adapter はこの型で
    ``isinstance`` 判定し、「必須バケット空(empty_series)」と「検証失敗(validation)」の二意味を
    日本語メッセージ片照合でなく型で区別する。ロードは sys.modules キャッシュ済みのため、
    invoke で送出された例外インスタンスの型と同一クラスオブジェクトを返す（isinstance が成立）。
    """
    return load_src_package("profit_band").EmptyBucketError
