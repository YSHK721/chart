"""framework 層: 外部技術（pydantic v2 / pyyaml）を境界に閉じる設定ローダ等。

CLEAN_ARCH §7/§9: framework は adapter/usecase/domain を import 可。pydantic v2・
pyyaml は本層の内部でのみ使用し、domain/usecase へ漏らさない。
"""
from __future__ import annotations
