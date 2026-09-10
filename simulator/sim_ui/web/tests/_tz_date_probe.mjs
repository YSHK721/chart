// 日付 → epoch 秒の変換が**プロセスのローカル TZ に依存しない**ことを確かめる実行体。
//
// なぜ独立した実行体か（工程 5 レビュー 🔴-4・実測）:
//   同一プロセス内で `new Date("...Z")` と突き合わせる検定は、**既定 TZ が UTC の環境では
//   何も検出しない**。`sim_submission_builder.js` の `Z` 付与を外す変異は TZ=UTC で
//   front 515 件すべて緑・TZ=Asia/Tokyo で 12 件赤（実測）。TZ を固定しない限り、
//   期待値をリテラル定数へ置き換えても検出力は上がらない（同じ環境で同じ値が出るため）。
//
//   したがって「ローカル TZ で解釈しない」という主張は、**非 UTC の TZ を明示した
//   子プロセス**で回して初めて検定になる。呼び手は `sim_trace_block.test.js`。
//
// 期待値は UTC 基準の epoch 秒リテラルである（実装から導かない＝実装が変われば赤になる）。
// ISSUE-401 で実測された 32,400 秒差（TZ=Asia/Tokyo）と同型の欠陥を、ここが受け止める。
import assert from "node:assert/strict";

import {
  epochSecondsOfDate, traceBlockOf,
} from "../js/adapter/front/sim_submission_builder.js";

// 正の対照: そもそも非 UTC で回っていなければ、この実行体は何も測っていない。
// （TZ=UTC で起動されたら失敗させる——「TZ を渡し忘れた呼び手」を検出する。）
const offsetMinutes = new Date("2024-06-15T00:00:00Z").getTimezoneOffset();
assert.notEqual(
  offsetMinutes, 0,
  `この実行体は非 UTC の TZ で起動される必要があります（現在のオフセット=${offsetMinutes} 分）。`
  + " TZ を渡し忘れると、ローカル TZ 解釈を 1 件も検出できません。",
);

// 日付のみ（UTC の 0 時）。画面の正準表記は `YYYY.MM.DD` なので両方を通す。
assert.equal(epochSecondsOfDate("2024-01-01"), 1704067200);
assert.equal(epochSecondsOfDate("2024.01.01"), 1704067200);
assert.equal(epochSecondsOfDate("2024.06.15"), 1718409600);  // 夏（DST のある TZ 対策）
// 時刻部あり・タイムゾーン指定なしも UTC。
assert.equal(epochSecondsOfDate("2024-01-01T01:30"), 1704072600);
assert.equal(epochSecondsOfDate("2024-01-01T00:00:59"), 1704067259);
// タイムゾーン指定ありは打たれたオフセットを尊重する（UTC 決め打ちではない）。
assert.equal(epochSecondsOfDate("2024-01-01T09:00:00+09:00"), 1704067200);

// 投入ブロックまで通しても同じ値になる（変換の実体が 1 つであることの確認）。
// 上端は「終了日を含む」半開区間なので、打たれた日の翌 0 時になる（front の裁定）。
assert.deepEqual(
  traceBlockOf({ enabled: true, from: "2024.01.01", to: "2024.01.02" }),
  { enabled: true, start: 1704067200, end: 1704240000 },
);
// 月またぎの上端もローカル TZ に依存しない（+1 日を Date のローカル演算で足すと崩れる）。
assert.equal(
  traceBlockOf({ enabled: true, from: "2025.01.31", to: "2025.01.31" }).end,
  Date.parse("2025-02-01T00:00:00Z") / 1000,
);

process.stdout.write("tz-probe: ok\n");
