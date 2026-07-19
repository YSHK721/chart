"""FakeChart（内部設計書 §3.3.2）— 既存 add_* を描画せず呼ぶための duck typing スタブ。

既存 add_* は ``create_line`` / ``horizontal_line`` を持つ chart を duck typing で
要求する（``tgp_btlm/src/lwc_chart.py:42-43``、``price_range_power/src/lwc_chart.py:32-33``）。
API 経路では描画せず「呼ばれた系列仕様を収集する Fake chart」を注入し、収集結果を
系列 JSON（§6.3.2 / §6.3.3）へ変換する（PORTING_GUIDE §7 と同手法）。

依存: 標準 + pandas（adapter 層なので利用可）。描画ライブラリは import しない。
"""

from __future__ import annotations

from typing import Any

import pandas as pd


def _to_unix_seconds(value: Any) -> int:
    """時刻値を UNIX 秒（整数）へ変換する（§6.3.2 time=UNIX 秒）。"""
    return int(pd.Timestamp(value).timestamp())


def _line_points(df: pd.DataFrame, value_column: str) -> list[dict[str, Any]]:
    """``time`` 列と値列を {time: UNIX 秒, value: float} の系列点 list へ変換する。

    系列 JSON の line.data 生成（§6.3.2）。NaN は呼び元で dropna 済み。
    ``color`` 列があれば per-point の色（histogram のバー別着色・add_adx_needle 等）を
    {time,value,color} として載せる（line 系列には color 列が無いため不変）。
    """
    has_color = "color" in df.columns
    points: list[dict[str, Any]] = []
    for _, row in df.iterrows():
        point = {"time": _to_unix_seconds(row["time"]), "value": float(row[value_column])}
        if has_color:
            color = row["color"]
            if color is not None and not (isinstance(color, float) and pd.isna(color)):
                point["color"] = color
        points.append(point)
    return points


class _FakeLine:
    """``create_line`` が返すライン。``set(df)`` で系列データを収集する。"""

    def __init__(self, name: str, **kwargs: Any) -> None:
        self.name = name
        self.kwargs = kwargs
        self.points: pd.DataFrame | None = None

    def set(self, df: pd.DataFrame) -> None:
        # add_btlm / add_profit_band は line.set(df) を呼ぶ（描画せず収集）。
        self.points = df


class FakeLineChart:
    """``create_line`` を持つ duck type（line 系指標：tgp_btlm / profit_band）。"""

    def __init__(self) -> None:
        self.lines: list[_FakeLine] = []

    def create_line(self, name: str, **kwargs: Any) -> _FakeLine:
        line = _FakeLine(name, **kwargs)
        self.lines.append(line)
        return line

    def legend(self, visible: bool = True) -> None:
        # add_profit_band の legend(visible=True) 呼び出しを吸収（lwc_chart.py:152-153）。
        del visible

    def to_payloads(self) -> list[dict[str, Any]]:
        """収集した各ラインを §6.3.2 line payload へ変換する。

        値列名 = name（series_name）なので name をそのまま JSON の name に載せる
        （source_column は外に漏れない・§申し送り3）。NaN は呼び元で dropna 済み。
        """
        payloads: list[dict[str, Any]] = []
        for line in self.lines:
            data = (
                _line_points(line.points, line.name)
                if line.points is not None
                else []
            )
            payload = {
                "name": line.name,
                "kind": "line",
                "style": line.kwargs.get("style"),
                "width": line.kwargs.get("width"),
                "color": line.kwargs.get("color"),
                "data": data,
            }
            # 描画ヒント（ドット/ライン切替）は付与された系列のみ載せる（後方互換: 既存
            #   指標は create_line にこれらを渡さないため payload に現れない＝挙動不変）。
            for hint in ("point_markers", "line_visible", "readout_only",
                         "point_markers_radius"):
                if hint in line.kwargs:
                    payload[hint] = line.kwargs[hint]
            payloads.append(payload)
        return payloads


class _FakeHLine:
    """``horizontal_line`` が収集する 1 水平線。"""

    def __init__(self, price: float, **kwargs: Any) -> None:
        self.price = price
        self.kwargs = kwargs


class FakeHorizontalChart:
    """``horizontal_line`` を持つ duck type（price_range_power）。"""

    def __init__(self) -> None:
        self.lines: list[_FakeHLine] = []

    def horizontal_line(self, price: float, **kwargs: Any) -> _FakeHLine:
        hl = _FakeHLine(price, **kwargs)
        self.lines.append(hl)
        return hl

    def to_payloads(self) -> list[dict[str, Any]]:
        """収集した水平線群を §6.3.3 horizontal_line payload（1 件）へ変換する。

        price_range_power は 1 指標 = 水平線群なので、payload を 1 件にまとめる。
        axis_label_visible は固定 false（lwc_chart.py:87,92）。
        """
        lines = [
            {
                "price": hl.price,
                "color": hl.kwargs.get("color"),
                "width": hl.kwargs.get("width"),
                "style": hl.kwargs.get("style"),
                "text": hl.kwargs.get("text"),
                "axis_label_visible": hl.kwargs.get("axis_label_visible", False),
            }
            for hl in self.lines
        ]
        return [
            {
                "name": "price_range_power",
                "kind": "horizontal_line",
                "lines": lines,
                "axis_label_visible": False,
            }
        ]


class _FakeSeries:
    """``create_line`` / ``create_histogram`` が返す系列。``set(df)`` で収集する。

    line / histogram の差は ``kind`` のみ（収集規約は共通＝値列名は name と一致）。
    histogram は per-point の ``color`` 列を持ちうる（_line_points が拾う）。
    """

    def __init__(self, name: str, kind: str, **kwargs: Any) -> None:
        self.name = name
        self.kind = kind
        self.kwargs = kwargs
        self.points: pd.DataFrame | None = None

    def set(self, df: pd.DataFrame) -> None:
        self.points = df


class FakeChart:
    """line / histogram / horizontal_line を一括収集する統合 duck type。

    既存 add_* は指標により ``create_line`` / ``create_histogram`` / ``horizontal_line``
    を任意に組み合わせて呼ぶ（オシレータ＝histogram or line ＋ 水準線）。FakeLineChart /
    FakeHorizontalChart は排他のためこれらを 1 指標内で併用できない。本クラスは 3 種を
    同一 chart で収集し、統合 payload（line/histogram は各 1 件、horizontal_line は群を 1 件に
    まとめ name=コンストラクタ ``name``）へ変換する。

    ``name``: horizontal_line 群 payload の name（= compute_id）。price_range_power は
    既存 FakeHorizontalChart と同一の ``name='price_range_power'`` を再現する。
    """

    def __init__(self, name: str = "indicator") -> None:
        self._name = name
        self.series: list[_FakeSeries] = []
        self.hlines: list[_FakeHLine] = []

    def create_line(self, name: str, **kwargs: Any) -> _FakeSeries:
        s = _FakeSeries(name, "line", **kwargs)
        self.series.append(s)
        return s

    def create_histogram(self, name: str, **kwargs: Any) -> _FakeSeries:
        s = _FakeSeries(name, "histogram", **kwargs)
        self.series.append(s)
        return s

    def horizontal_line(self, price: float, **kwargs: Any) -> _FakeHLine:
        hl = _FakeHLine(price, **kwargs)
        self.hlines.append(hl)
        return hl

    def legend(self, visible: bool = True) -> None:
        # add_profit_band の legend(visible=True) 呼び出しを吸収。
        del visible

    def to_payloads(self) -> list[dict[str, Any]]:
        """収集系列を §6.3.2/6.3.3 payload へ変換する（line/histogram 各 1 件・水平線群 1 件）。"""
        payloads: list[dict[str, Any]] = []
        for s in self.series:
            data = _line_points(s.points, s.name) if s.points is not None else []
            payloads.append(
                {
                    "name": s.name,
                    "kind": s.kind,
                    "style": s.kwargs.get("style"),
                    "width": s.kwargs.get("width"),
                    "color": s.kwargs.get("color"),
                    "data": data,
                }
            )
        if self.hlines:
            lines = [
                {
                    "price": hl.price,
                    "color": hl.kwargs.get("color"),
                    "width": hl.kwargs.get("width"),
                    "style": hl.kwargs.get("style"),
                    "text": hl.kwargs.get("text"),
                    "axis_label_visible": hl.kwargs.get("axis_label_visible", False),
                }
                for hl in self.hlines
            ]
            payloads.append(
                {
                    "name": self._name,
                    "kind": "horizontal_line",
                    "lines": lines,
                    "axis_label_visible": False,
                }
            )
        return payloads
