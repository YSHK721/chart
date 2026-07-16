"""usecase 層（ISSUE-092 ①）— Application Business Rules。

/compute の業務手順（compute_indicators）と、それが所有する Output Boundary
（DatasetPort）を提供する。本層は marketdata / adapter を module-level import しない
（回帰ガード: api/tests/test_no_usecase_dependency.py）。
"""

from __future__ import annotations

from usecase.compute_indicators import (
    ComputeRequest,
    ComputeResult,
    compute_indicators,
)
from usecase.dataset_port import DatasetPort, dataset_port, set_dataset_port

__all__ = [
    "ComputeRequest",
    "ComputeResult",
    "compute_indicators",
    "DatasetPort",
    "dataset_port",
    "set_dataset_port",
]
