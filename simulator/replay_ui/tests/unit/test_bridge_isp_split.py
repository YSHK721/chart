"""ISSUE-136（ISP）: bridge の粒度別アクセサ分割と dataset 具象の役割別 狭いポート化の回帰ガード。

- ``load_dataset`` / ``load_compute`` / ``load_mp_handlers`` が期待面のみを束ねる。
- dataset のみ経路（intrabar / causal_candle）が MP controller を **eager import しない**
  ことをクリーン interpreter（subprocess）で実証する（本 Issue の遮断検証）。
- ``marketdata.dataset`` 具象が役割別 狭いポート（RefValidationPort / OhlcSupplyPort）を
  構造的に満たす（クライアントが狭い型で受けても挙動不変）。
"""
from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path

from simulator.replay_ui.adapter import _indicator_ui_bridge as bridge
from simulator.replay_ui.adapter.dataset_ports import OhlcSupplyPort, RefValidationPort

_REPO_ROOT = Path(__file__).resolve().parents[4]


def test_load_dataset_exposes_only_dataset_face():
    ns = bridge.load_dataset()
    assert hasattr(ns, "dataset")
    assert not hasattr(ns, "handle_market_profile")
    assert not hasattr(ns, "full_compute")


def test_load_compute_exposes_dataset_and_compute_faces():
    ns = bridge.load_compute()
    for name in ("dataset", "adapter", "full_compute", "latest_compute"):
        assert hasattr(ns, name), name
    assert not hasattr(ns, "handle_market_profile")


def test_load_mp_handlers_exposes_only_mp_face():
    ns = bridge.load_mp_handlers()
    assert hasattr(ns, "handle_market_profile")
    assert hasattr(ns, "handle_market_profile_forming")
    assert not hasattr(ns, "dataset")


def test_backward_compat_load_still_unions_all_faces():
    ns = bridge.load()
    for name in (
        "dataset",
        "adapter",
        "full_compute",
        "latest_compute",
        "handle_market_profile",
        "handle_market_profile_forming",
    ):
        assert hasattr(ns, name), name


def test_load_dataset_does_not_eager_import_mp_controllers():
    """dataset-only 経路が MP controller モジュールを import しないことをクリーン interpreter で実証。

    同一プロセスでは他テストが MP を先に import している可能性があるため、subprocess で隔離する。
    """
    code = textwrap.dedent(
        """
        import sys
        from simulator.replay_ui.adapter import _indicator_ui_bridge as b
        ns = b.load_dataset()
        assert hasattr(ns, "dataset")
        leaked = [m for m in sys.modules if m.startswith("market_profile_api.controller")]
        assert not leaked, "dataset-only 経路が MP controller を eager import: " + repr(leaked)
        print("OK")
        """
    )
    r = subprocess.run(
        [sys.executable, "-c", code], cwd=str(_REPO_ROOT), capture_output=True, text=True
    )
    assert r.returncode == 0, f"stdout={r.stdout!r} stderr={r.stderr!r}"
    assert "OK" in r.stdout


def test_dataset_concrete_satisfies_narrow_role_ports():
    """dataset 具象が RefValidationPort / OhlcSupplyPort を構造的に満たす（狭い型受けで挙動不変）。"""
    ns = bridge.load_dataset()
    assert isinstance(ns.dataset, RefValidationPort)
    assert isinstance(ns.dataset, OhlcSupplyPort)
