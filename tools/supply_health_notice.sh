#!/usr/bin/env bash
# supply_health_notice.sh — 供給の健全性を起動時に可視化する（ISSUE-526 段 3）。
#
#   使い方: supply_health_notice.sh <python> <repo_root>
#
# 終了コードは**常に 0** である。これは規約であって手抜きではない:
#   供給が止まっているときに UI の起動まで止めると「供給が止まっているから画面も開けない」に
#   なり、原因を調べる手段ごと失う。告知は気づける形であって門ではない。呼び出し側
#   （unified_ui/serve.sh）は `set -e` で走るため、ここが 0 以外を返すと起動が止まる。
#
# なぜログだけにしないか:
#   unified_ui/serve.sh の core 起動は子の出力を捨てており、失敗の理由が残らない（ISSUE-527 の
#   実在する非対称）。同じ形を新設すると、告知を足したのに誰も見ない。よって起動時の標準出力・
#   標準エラーへ出し、判定を得られなかった場合もその理由を捨てない（stderr を 2>&1 で拾う）。
#
# 判定の規則はここに 1 行も無い。規則は marketdata/supply_health.py の単一の定義が持ち、本
# スクリプトはそれを実行可能モジュールとして呼ぶだけである（shell へ python のコード片を
# 埋め込まない＝規則の第 2 の写しを作らない）。
#
# 検定: tools/tests/test_supply_health_notice.py が本スクリプトを実際に走らせて、
#   「異常な判定のときだけ鳴る」「どの判定でも終了コードは 0」「理由を捨てない」を測る。
set -u

PY="${1:-python3}"
ROOT="${2:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"

# 判定は 1 回だけ求める。stderr も一緒に拾うのは、判定を得られなかったときに理由を残すため。
report="$(PYTHONPATH="${ROOT}${PYTHONPATH:+:${PYTHONPATH}}" "$PY" -m marketdata.supply_health 2>&1)"

# 読む側は 1 行目だけで判断できる（/__serving_root と同じ様式）。
overall="$(printf '%s\n' "$report" | head -n 1)"

case "$overall" in
  # 鳴らさない判定。判定不能を鳴らさないのは、観測を始めた直後は必ず判定不能になる（前回観測が
  #   無い）ためで、鳴りっぱなしの告知は誰も見なくなる。語彙が増えたときは鳴る側へ落ちるので、
  #   取り残しが「黙る」方向へは倒れない（一致は上記の検定が全語彙で突き合わせる）。
  healthy|undecidable)
    echo "  供給の健全性: ${overall}"
    ;;
  *)
    echo "⚠ 警告: 供給の健全性が健全ではありません（判定: ${overall:-(応答なし)}）。UI の起動は続けます。" >&2
    if [ -n "$report" ]; then
      printf '%s\n' "$report" | sed 's/^/       /' >&2
    fi
    echo "       stuck = 書き手は在るのに先端が進まない（詰まりを探す） / stopped = 正規の書き手が居ない（起こし直す）" >&2
    ;;
esac

exit 0
