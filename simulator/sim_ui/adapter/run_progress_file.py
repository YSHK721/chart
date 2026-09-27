"""job-dir の進み具合ファイル（`progress.json`）の書き手と読み手（唯一の定義）。

書くのはジョブの子プロセス（`RunProgressRecorder` の知らせ先）、読むのは台帳
（`FileJobLedger.read_progress`）。ファイル名と形式をここ 1 箇所に置き、書き手と読み手で
別々に持たない。

書き方: 同じディレクトリの一時ファイルへ書いてから置き換える（同一 FS の原子的置換）。
    照会側が書きかけのファイルを読まない（`FileJobLedger._write_state` と同じ理由）。
    書けなくても run の結果は変えない（進み具合は表示のためのもの）。
"""
from __future__ import annotations

import json
import uuid
from pathlib import Path

PROGRESS_FILE = "progress.json"


def write_progress(job_dir: Path, percent: int) -> None:
    """``{"percent": n}`` を原子的に書く（失敗しても例外を上げない）。"""
    target = Path(job_dir) / PROGRESS_FILE
    tmp = target.with_name(f"progress.{uuid.uuid4().hex}.tmp")
    try:
        tmp.write_text(json.dumps({"percent": int(percent)}), encoding="utf-8")
        tmp.replace(target)
    except OSError:
        pass
    finally:
        if tmp.exists():
            tmp.unlink(missing_ok=True)


def read_progress(job_dir: Path) -> "int | None":
    """書かれた％を返す。無い・読めない・形が違うときは None（捏造しない）。"""
    path = Path(job_dir) / PROGRESS_FILE
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    value = data.get("percent") if isinstance(data, dict) else None
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 100:
        return None
    return value
