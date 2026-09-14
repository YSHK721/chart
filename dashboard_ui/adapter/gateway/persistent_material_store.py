"""確定素材のディスク持ち越し（ISSUE-501 段階 2・依頼者承認 2026-09-06）。

なぜ要るか（実測）:
    確定足由来の素材は再起動の前後で不変（版＝epoch 指紋が同じなら同じ物）なのに、
    内側の素材ストアはプロセス寿命のため、再起動のたびに全素材を再発行していた。
    コールド 1 要求 5.49 秒のうち確定系列の再計算がその律速である——出力は正しいままなので
    状態検証では原理的に落ちない（ISSUE-450 / ISSUE-457 と同型の「作ってから捨てる」）。

何を持ち越すか:
    **プリミティブ形（dict / list / tuple / str / 数値 / None）だけ**である。クラス実体
    （dataclass・ndarray）は pickle でも書けるが、コード改版後の読み戻しで**例外なく形が
    ずれ得る**（属性の増減が黙って通る）ため対象にしない——形がコード版数と独立な物だけを
    ディスクに置く。対象外の素材は従来どおり内側ストア（プロセス寿命）に載るだけで、
    正しさは変わらない（速さだけが従来のまま）。

鮮度（古い素材を配らない）:
    版の判定は内側ストアと同一の epoch（確定素材の指紋）を**そのまま**使う。読み戻しは (key, name, epoch) の三つ組が完全一致した場合だけであり、不一致・
    読み取り失敗・破損はすべて factory の再計算へ倒す（フェイルオープン＝最悪でも従来挙動）。

書き込みの原子性:
    同名一時ファイル → `os.replace`（POSIX で原子的）。途中クラッシュで破損ファイルが
    残らない。書き込み失敗は握りつぶす（キャッシュはあくまで速さの装置であり、
    読み取り専用 FS 等でシート供給を落とさない）。
"""
from __future__ import annotations

import hashlib
import os
import pickle
import tempfile
from pathlib import Path
from typing import Any, Callable, Hashable

#: ディスクへ置いてよい葉の型（コード版数と独立にシリアライズが安定する物だけ）。
_PLAIN_LEAVES = (str, int, float, bool, bytes, type(None))

#: 封筒の形式版。読み戻しの互換判定に使う（形式を変えたら上げる＝旧ファイルは再計算へ）。
_FORMAT = 1


def default_spill_dir() -> Path:
    """既定の置き場（`cache/market_profile_dwell/` と同じ規約: DATA_DIR 配下・git 追跡外）。

    DATA_DIR（素材の単一基点）を知るのは adapter 層の仕事である（技術隔離 R3）。
    """
    from marketdata.paths import DATA_DIR   # 遅延: ベンダ非依存だが import 時依存を作らない

    return Path(DATA_DIR) / "cache" / "reach_sheet_materials"


def _is_plain(value: Any) -> bool:
    """プリミティブ形（入れ子可）だけ True。クラス実体・ndarray は False。"""
    if isinstance(value, _PLAIN_LEAVES):
        return True
    if isinstance(value, (list, tuple)):
        return all(_is_plain(item) for item in value)
    if isinstance(value, dict):
        return all(
            _is_plain(k) and _is_plain(v) for k, v in value.items()
        )
    return False


class PersistentMaterialStore:
    """素材ストアと同面のデコレータ: 素材をディスクへ書き、再起動後に読み戻す。

    内側ストアの錠・版管理はそのまま使う（factory はキー錠の中で 1 回だけ呼ばれる）。
    本クラスは factory の**内側**に入り、「作る前にディスクを見る／作ったら書く」だけを足す
    （OCP: 既存ストア・既存 gateway は無改変）。
    """

    def __init__(self, inner: Any, *, spill_dir: "Path | str") -> None:
        self._inner = inner
        self._dir = Path(spill_dir)

    def material(
        self, *, key: Hashable, epoch: Hashable, name: Hashable,
        factory: Callable[[], Any],
    ) -> Any:
        return self._inner.material(
            key=key, epoch=epoch, name=name,
            factory=lambda: self._recall_or_make(key, epoch, name, factory),
        )

    # ------------------------------------------------------------------ 内部
    def _recall_or_make(
        self, key: Hashable, epoch: Hashable, name: Hashable,
        factory: Callable[[], Any],
    ) -> Any:
        path = self._path_of(key, name)
        recalled = self._read(path, key, epoch, name)
        if recalled is not None:
            return recalled[0]
        value = factory()
        self._write(path, key, epoch, name, value)
        return value

    def _path_of(self, key: Hashable, name: Hashable) -> Path:
        digest = hashlib.sha1(repr((key, name)).encode("utf-8")).hexdigest()
        return self._dir / f"{digest}.pkl"

    def _read(
        self, path: Path, key: Hashable, epoch: Hashable, name: Hashable,
    ) -> "tuple[Any] | None":
        """(key, name, epoch) 完全一致なら `(素材,)`。それ以外は None（再計算へ）。"""
        try:
            with open(path, "rb") as stream:
                envelope = pickle.load(stream)
            if (
                isinstance(envelope, tuple) and len(envelope) == 5
                and envelope[0] == _FORMAT
                and envelope[1] == key and envelope[2] == name
                and envelope[3] == epoch
            ):
                return (envelope[4],)
        except Exception:   # noqa: BLE001 — 破損・不在・版ずれはすべて再計算へ倒す
            pass
        return None

    def _write(
        self, path: Path, key: Hashable, epoch: Hashable, name: Hashable, value: Any,
    ) -> None:
        if not (_is_plain(value) and _is_plain(key) and _is_plain(name)
                and _is_plain(epoch)):
            return   # コード版数に依存する形は持ち越さない（内側ストアだけに載せる）
        try:
            self._dir.mkdir(parents=True, exist_ok=True)
            payload = pickle.dumps(
                (_FORMAT, key, name, epoch, value), protocol=pickle.HIGHEST_PROTOCOL
            )
            fd, tmp = tempfile.mkstemp(dir=self._dir, suffix=".tmp")
            try:
                with os.fdopen(fd, "wb") as stream:
                    stream.write(payload)
                os.replace(tmp, path)
            except BaseException:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
                raise
        except Exception:   # noqa: BLE001 — 書けない環境でもシート供給は落とさない
            pass
