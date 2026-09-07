// tf_meta.js（domain）— 時間足メタの公開面。**値は持たず、生成台帳から導出する**。
//
// 旧状態(1): TF_BAR_SEC/TF_SECONDS が market_profile_actor.js・growth_window.js・
//   live_tick_player.js・composition_root_front.js に 4 重定義され、バンドラ（build.mjs の
//   IIFE 連結）の top-level const 衝突が共有を阻害していた（ISSUE-087 🔴-2）。本モジュールへ
//   一本化し、全利用側は import する（連結後も定義は 1 箇所＝衝突しない）。
//
// 旧状態(2)（ISSUE-254）: 一本化したのは **値（barSec）だけ**で、派生属性は JS 側に手書きの
//   配列（FLOOR_TFS）として残っていた。値の一致は parity 検定（py_parity_golden）が守って
//   いたが、派生属性は検定の対象外だったため静かにずれる余地があった。実際 floorable の写しが
//   ずれて、ライブの更新粒度が時間足で割れた（ISSUE-253: 1W/1M だけ tick 再生から脱落）。
//   よって**台帳そのもの**（code/barSec/floorable/calendar）を Python から生成して配り、
//   本モジュールはそこからの導出だけを行う。判断材料を JS 側に書かない。
//
// 唯一の定義: marketdata/resample.py の TF_DESCRIPTORS ＋ marketdata/tf_meta.py の TF_BAR_SEC。
//   生成物 tf_ledger_generated.js は tools/gen_js_parity_golden.py が書き、陳腐化は
//   py_parity_golden.test.js（JS 側）と test_tf_ledger_parity.py（Python 側）が双方向に落とす。

import { TF_LEDGER } from './tf_ledger_generated.js';

// 台帳の順序どおりの時間足コード（時間足メニュー・検定の反復順の基準）。
export const TF_CODES = Object.freeze(TF_LEDGER.map((d) => d.code));

// tf → バー秒長（名目値）。1W/1M はカレンダー周期のため名目（7日/30日）で、厳密な期間境界は
//   ラベル規約（session_day/resample）が担う＝本表は窓幅・表示近似の用途に限る。
export const TF_BAR_SEC = Object.freeze(
  Object.fromEntries(TF_LEDGER.map((d) => [d.code, d.barSec])),
);

// 既知 tf か（台帳のキー集合＝Python marketdata.resample.is_known_timeframe と同一）。
//   ライブ tick 再生・足内更新は**全時間足で同一設計**（ISSUE-253）のため、対応判定はこれだけ。
//   バー帰属（どの時刻がどのバーか）はサーバの唯一源が解決して配るので、フロントは
//   floor 可否・周期秒・暦周期といった tf ごとの区別を更新経路に持たない。
export function isKnownTimeframe(tf) {
  return Object.prototype.hasOwnProperty.call(TF_BAR_SEC, tf);
}

// 固定周期（単純 floor で期間始端を表せる）tf。**ライブの更新経路では使わない**
//   （使うと tf ごとに設計が割れる＝ISSUE-253 の再発）。残る用途は「floor で窓を切ってよいか」を
//   問う近似計算のみ（MP 成長窓など）。値は台帳の floorable からの導出。
export const FLOOR_TFS = Object.freeze(TF_LEDGER.filter((d) => d.floorable).map((d) => d.code));

export function isFloorTimeframe(tf) {
  return FLOOR_TFS.includes(tf);
}

// セッション日（ブローカー暦日）で集計する上位足か。台帳の calendar からの導出。
export const CALENDAR_TFS = Object.freeze(TF_LEDGER.filter((d) => d.calendar).map((d) => d.code));

export function isCalendarTimeframe(tf) {
  return CALENDAR_TFS.includes(tf);
}

// 暦ラベル足（期間の**右端**がラベルになる tf＝1W/1M 相当）。台帳の calendal かつ非 floorable
//   からの導出で、Python 側 marketdata/resample.py の
//   `code for code, d in TF_DESCRIPTORS.items() if d.calendar and not d.floorable` と同じ式。
//   ISSUE-278 #13: リプレイ側が `new Set(['1W','1M'])` や `tf === '1W' || tf === '1M'` を手書きで
//   持っており、台帳へ暦足を足しても追随しなかった（追随しない側は足内窓の切り方と MP 成長の
//   分岐を誤り、エラーを出さずに前回描画を保持する）。判断材料を JS 側に書かない。
export const CALENDAR_LABEL_TFS = Object.freeze(
  TF_LEDGER.filter((d) => d.calendar && !d.floorable).map((d) => d.code),
);

export function isCalendarLabelTimeframe(tf) {
  return CALENDAR_LABEL_TFS.includes(tf);
}
