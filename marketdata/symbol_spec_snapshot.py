"""symbol_spec_snapshot — MT5 端末スナップショットから銘柄仕様を読む唯一の経路。

由来: ISSUE-445 恒久策 **段階 2** ／ ``.doc/SYMBOL_SPEC_SUPPLY_BASIC_DESIGN.md`` §3.2・§3.4。

## 何を解くのか（RC-1）

ISSUE-445 で判明した誤りは「値が 1 つ間違っていたこと」ではなく、**供給元が出力していない値を
人が権威台帳へ書き足せる構造**である。実際 ``case.yaml`` の ``contract_size: 10`` は MT5 レポート
に一度も現れない逆算値であり、fixture 作成（2026-06-18）からライブ実接続（2026-08-25）まで
2 か月以上検出されなかった。

本モジュールは供給元（``tools/capture_mt5_symbol_spec.py`` が MT5 端末から丸ごと落とした
スナップショット）を**読むだけ**の層である。ここに値を書かない・既定値で埋めない。
引けないキーは :class:`SnapshotError` で中断する（黙って 0 を返すと RC-1 が再生する）。

## 対応表は 1 箇所だけに置く

MT5 のフィールド名（``trade_contract_size`` / ``trade_stops_level`` / ``point`` …）と
``simulator/usecase/models.py:SymbolSpec`` の 8 フィールド名（``contract_size`` /
``stops_level`` / ``point_size`` …）は綴りが違う。この対応が複数箇所に散ると、片方だけ直した
ときに沈黙で食い違う。よって対応は :data:`SPEC_FIELD_SOURCES` と
:data:`SETTLEMENT_CURRENCY_SOURCE` に**限る**。この不変条件は宣言ではなく
``marketdata/tests/test_symbol_spec_snapshot.py`` の AST 走査が施行する
（MT5 フィールド名のリテラルがモジュール内に 2 箇所現れたら赤）。

``leverage`` は ``symbol`` ではなく ``account`` セクションから引く。``mt5.symbol_info()`` に
``leverage`` は存在しない（ISSUE-445 実測・スナップショットの ``symbol`` 96 フィールドに無い）。
``leverage`` が銘柄仕様ではなく口座属性であること自体は SRP 違反として残るが、
``SymbolSpec`` からの分離は既存 IF に触れるため**段階 3**（設計書 §3.4）。

## 依存方向

**依存ゼロ**（stdlib のみ）。``marketdata/symbol_spec.py`` および ``dataset_registry.py`` が
宣言する最下層規律に合わせる。ここに ``pandas`` や ``paths`` を持ち込むと「pandas を使えない
純層は同じ台帳を引けない」が発生する（ISSUE-261 と同型）。

スナップショットの所在は**パッケージ内の実在ディレクトリ**であり、スクリプト位置からの推測では
ない（``tools/capture_mt5_symbol_spec.py:find_repo_root`` の事故と同型の誤りを作らない）。
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Callable, Mapping

#: スナップショットの所在（``tools/capture_mt5_symbol_spec.py`` の既定出力先と同じ配置）。
SNAPSHOT_ROOT: Path = Path(__file__).resolve().parent / "symbol_specs"

#: 取り込み済みの供給元（ディレクトリ名）。サーバ名は ``sanitize_path_component`` 済みの形。
#: 供給元が増えたらここに列挙を足すのではなく、呼び出し側がサーバ名を渡す（OCP）。
OANDA_JAPAN_MT5_LIVE = "OANDA-Japan-MT5-Live"


class SnapshotError(RuntimeError):
    """スナップショットから引けなかったことを表す（Fail-Stop）。既定値で埋めない。"""


@dataclass(frozen=True)
class FieldSource:
    """1 フィールドの供給元（どのセクションのどのキーを、どの型で読むか）。"""

    section: str
    key: str
    cast: "Callable[[Any], Any]"


#: MT5 フィールド名 → ``SymbolSpec`` の 8 フィールド名。**対応表はここだけ**（module docstring）。
SPEC_FIELD_SOURCES: "Mapping[str, FieldSource]" = MappingProxyType(
    {
        "contract_size": FieldSource("symbol", "trade_contract_size", float),
        "volume_min": FieldSource("symbol", "volume_min", float),
        "volume_max": FieldSource("symbol", "volume_max", float),
        "volume_step": FieldSource("symbol", "volume_step", float),
        "stops_level": FieldSource("symbol", "trade_stops_level", int),
        "digits": FieldSource("symbol", "digits", int),
        "point_size": FieldSource("symbol", "point", float),
        # 口座属性（symbol_info に leverage は無い・設計書 §3.4）。
        "leverage": FieldSource("account", "leverage", float),
    }
)

#: 決済（profit）通貨。``SymbolSpec`` の 8 フィールドには含まれないため別に置く
#: （``RunProfile.settlement_currency`` が突き合わせる N-11 の判定データ源）。
SETTLEMENT_CURRENCY_SOURCE = FieldSource("symbol", "currency_profit", str)


def snapshot_path(server: str, symbol: str) -> Path:
    """スナップショットの所在を返す（ファイルの実在は問わない）。"""
    return SNAPSHOT_ROOT / server / f"{symbol}.json"


def load_snapshot(server: str, symbol: str) -> "dict[str, Any]":
    """スナップショット JSON を読む。無ければ :class:`SnapshotError`。"""
    path = snapshot_path(server, symbol)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise SnapshotError(
            f"銘柄仕様スナップショットが読めません: {path}"
            f"（server={server!r} / symbol={symbol!r}）。"
            " tools/capture_mt5_symbol_spec.py を MT5 端末上で実行して取得してください。"
        ) from exc
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise SnapshotError(f"スナップショットが JSON として読めません: {path}（{exc}）") from exc


def _pick(snapshot: "Mapping[str, Any]", source: FieldSource, name: str) -> Any:
    """対応表 1 エントリ分を引く。引けなければ中断する（既定値を作らない）。"""
    section = snapshot.get(source.section)
    if not isinstance(section, Mapping):
        raise SnapshotError(
            f"{name}: スナップショットに {source.section!r} セクションがありません"
        )
    if source.key not in section:
        raise SnapshotError(
            f"{name}: {source.section}.{source.key} がスナップショットにありません。"
            " 供給元（MT5 端末）で再取得してください。"
        )
    try:
        return source.cast(section[source.key])
    except (TypeError, ValueError) as exc:
        raise SnapshotError(
            f"{name}: {source.section}.{source.key}={section[source.key]!r} を"
            f" {source.cast.__name__} へ変換できません"
        ) from exc


def spec_fields(snapshot: "Mapping[str, Any]") -> "dict[str, Any]":
    """``SymbolSpec`` の 8 フィールド名をキーに持つ dict を返す。

    ``simulator.usecase.models.SymbolSpec`` を返さないのは依存方向のため
    （``marketdata`` は最下層であり ``simulator`` を知らない）。呼び出し側が
    ``build_interactor(**spec_fields(...))`` のように展開して使う。
    """
    return {name: _pick(snapshot, source, name) for name, source in SPEC_FIELD_SOURCES.items()}


def settlement_currency(snapshot: "Mapping[str, Any]") -> str:
    """決済（profit）通貨。"""
    return _pick(snapshot, SETTLEMENT_CURRENCY_SOURCE, "settlement_currency")


def load_spec_fields(server: str, symbol: str) -> "dict[str, Any]":
    """スナップショットを読んで 8 フィールドを返す（読み込み + 写像の合成）。"""
    return spec_fields(load_snapshot(server, symbol))


def load_settlement_currency(server: str, symbol: str) -> str:
    """スナップショットを読んで決済通貨を返す。"""
    return settlement_currency(load_snapshot(server, symbol))


__all__ = [
    "SNAPSHOT_ROOT",
    "OANDA_JAPAN_MT5_LIVE",
    "SnapshotError",
    "FieldSource",
    "SPEC_FIELD_SOURCES",
    "SETTLEMENT_CURRENCY_SOURCE",
    "snapshot_path",
    "load_snapshot",
    "spec_fields",
    "settlement_currency",
    "load_spec_fields",
    "load_settlement_currency",
]
