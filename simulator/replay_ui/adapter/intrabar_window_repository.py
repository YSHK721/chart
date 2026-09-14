"""IntrabarWindowRepository — /intraday の IntrabarWindowPort 実装（proto do_intraday 忠実）。

m1  : 区間 [start,end) の 1 分足 OHLC 行（``[o,h,l,c]``）。供給は **dataset の単一権威**
      ``dataset.load_atom_window``（全期間原子・clamp 外れ値補正・mtime キャッシュ・ISSUE-132）へ
      完全委譲する（旧: 生 CSV 全読み＋独自 repair＋独自キャッシュの第二経路を全廃）。上位足の
      ペイロードは ``_cap_m1_rows`` で 1500 行へ間引く（先頭/末尾＋窓内 高値最大/安値最小は必ず残す）。
ticks: 区間 [start,end) の実ティック ``(sec, price)``。ref のティック木（台帳）の日別ファイルを
      [start,end) 跨ぎで走査し、ref の価格基準（台帳）で畳んで返す（窓フィルタ＋中央値外れ値除去は
      usecase・ISSUE-031・cap 無し）。ISSUE-512 段階 4 の前提で ref を受け取るようにした。

技術隔離（CLEAN_ARCH §6）: pandas / parquet IO は本ファイル内に閉じる。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

import pandas as pd

from indigators.indicator_ui import api_loader
# timestamp → epoch 秒の唯一実体（ISSUE-410: 規則を書き写さず同一オブジェクトを読む）。
from simulator.adapter.repository.tick_parquet import timestamp_epoch_seconds
from simulator.replay_ui.adapter.dataset_ports import OhlcSupplyPort

_M1_CAP = 1500


def _cap_m1_rows(rows: "list[list[float]]", n: int) -> "list[list[float]]":
    """m1 OHLC 行を最大 n 行へ間引く（proto _cap_m1_rows と bit 一致）。

    先頭/末尾＋窓内 高値最大(idx1)/安値最小(idx2) の行を必ず残す。1D 以下（≤n）は無変更。
    """
    if len(rows) <= n:
        return rows
    i_hi = max(range(len(rows)), key=lambda i: rows[i][1])  # high 最大
    i_lo = min(range(len(rows)), key=lambda i: rows[i][2])  # low 最小
    keep = {0, len(rows) - 1, i_hi, i_lo}
    stride = len(rows) / n
    for k in range(n):
        keep.add(int(k * stride))
    return [rows[i] for i in sorted(keep)]


class IntrabarWindowRepository:
    """IntrabarWindowPort 実装。m1 は dataset 委譲・tick は parquet 直読（リプレイ固有フィード）。"""

    def __init__(
        self,
        tick_root: Any,
        api_path: Any = None,
        repo_root: Any = None,
        m1_cap: int = _M1_CAP,
        bridge_loader: "Callable[..., Any] | None" = None,
    ) -> None:
        self._tick_root = Path(tick_root)
        self._api_path = api_path
        self._repo_root = repo_root
        self._m1_cap = m1_cap
        # 既定は dataset のみのアクセサ（ISSUE-136 ISP: MP controller を eager import しない）。
        # テストは fake loader を注入（MarketProfileGateway と同型）。
        self._loader = (
            bridge_loader if bridge_loader is not None else api_loader.load_dataset
        )

    # ---- IntrabarWindowPort ----

    def load_m1_rows(self, ref: str, start: int, end: int) -> "list[list[float]]":
        bridge = self._loader(self._api_path, self._repo_root)
        # ISSUE-136 ISP: OHLC 供給の狭いポート型で受ける（load_atom_window の 1 面のみに依存）。
        ohlc: OhlcSupplyPort = bridge.dataset
        sub = ohlc.load_atom_window(ref, start, end)
        rows = [
            [float(r.open), float(r.high), float(r.low), float(r.close)]
            for r in sub.itertuples(index=False)
        ]
        return _cap_m1_rows(rows, self._m1_cap)

    def load_tick_prices(self, ref: str, start: int, end: int) -> "list[tuple[int, float]]":
        """``ref`` の [start,end) を跨ぐ日別ティックから ``(sec, price)`` を組む（窓も外れ値も落とさない）。

        ISSUE-512 段階 4 の前提（ライブと同じ規則・台帳が唯一の出所）:
          - どの木を読むか: 台帳の ``tick_tree_token(ref)``。木を持たない ref は空（他の ref の木を
            読まない）。以前は ref を受け取らず、どの ref でも Dukascopy の木を読んでいた。
          - どの日のファイルを読むか: :func:`marketdata.tick_day_source.day_tick_files`（確定
            parquet、無ければ MT5 の受信ジャーナル）。窓と重なる日だけを読む。
          - 価格の畳み方: 台帳の ``tick_price_basis(ref)`` を :func:`marketdata.tick_m1.ts_and_price`
            （唯一の規則）へ渡す。以前は domain が mid を自前で持っていた。
        ISSUE-031: 窓フィルタと外れ値除去は usecase 側で適用する（本 adapter は保管形式 →
        価格の変換に閉じる）。
        """
        from marketdata import tick_day_source, tick_m1
        from marketdata.tf_meta import tick_price_basis, tick_tree_token

        tree = tick_tree_token(ref)
        if tree is None:
            return []
        basis = tick_price_basis(ref)
        d0 = datetime.fromtimestamp(start, tz=timezone.utc).date()
        d1 = datetime.fromtimestamp(max(start, end - 1), tz=timezone.utc).date()
        files = tick_day_source.day_tick_files(
            d0, d1, symbol=tree, data_dir=self._tick_root.parent
        )
        frames = [tick_day_source.read_day_ticks(p, tick_m1.TICK_COLUMNS) for p in files]
        if not frames:
            return []
        tdf = pd.concat(frames, ignore_index=True) if len(frames) > 1 else frames[0]
        ts, price = tick_m1.ts_and_price(tdf, price_basis=basis)
        # epoch 秒化は共有実体 timestamp_epoch_seconds へ委譲（規則の写しを持たない・
        # ISSUE-410。aware/naive・解像度 ms/us/ns の正規化は共有実体が持つ）。
        secs = timestamp_epoch_seconds(ts).to_numpy()
        return [(int(s), float(p)) for s, p in zip(secs, price.tolist())]
