// replay/timing.js — 再生テンポ純ロジック（DOM/lwc/timer/fetch 非依存）。
//
// 参照実装＝プロト web/js/replay.js（挙動の正解定義）から、速度/フレーム間隔/足内ステップ/
//   ETA モデル推定/EMA 平滑の「値と式」だけを抽出したもの。分岐・境界・数式を 1つも足さず/削らず。
//   副作用（DOM 読取・setTimeout）は import しない（View 側に残す）。

// --- 定数（replay.js の同名定数と bit 一致） ------------------------------------- //
export const MIN_SPEED = 0.05;      // effSpeed の下限（0除算/Infinity 回避。0保持はゲート側）
export const BASE_FRAME_MS = 50;    // s=1（最速）時の足送り間隔。1/s で延伸。
export const PER_POINT_MS = 6;      // 1点あたりのステップ間隔（総時間＝点数×PER_POINT_MS）
export const ANIM_FINE = 800;       // 実ティック/全ティック合成の点数上限
export const ANIM_COARSE = 200;     // 1分OHLC の点数上限
export const ANIM_MIN_MS = 5;       // 1ステップ最小間隔（速すぎ防止）
export const FORMING_MIN_INTERVAL_MS = 120; // 足内 MA 追従の最小間隔

// --- 実時間再生「リアルタイム」（依頼者指示 2026-08-01・参照実装に対応物なしの新規拡張） --------- //
//   既存の速度 0.00〜1.00 は「最速（=1.00）に対する遅さの比」であって時間の比ではない（1足あたり
//   の所要は点数×PER_POINT_MS で決まり、時間足の長さと無関係）。「リアルタイム」は市場時刻 1 秒を実時間
//   1 秒で流す別軸のテンポ＝1足の所要は時間足の長さそのもの（1m→60秒）。よって比の値域には
//   収まらず、数値でない番兵値で表す（clampSpeed はこれを素通しする）。
export const REALTIME = 'realtime';
export function isRealtime(v) { return v === REALTIME; }

// 速度の parse は `||1` を使わない（0 は falsy で 0||1=1＝「0.00で停止しない」バグの原因）。
//   NaN/非有限のみ既定 1 へ。0 は 0 のまま許容＝一時停止。（replay.js: speed()）
export function clampSpeed(v) {
  if (isRealtime(v)) return REALTIME; // 実時間テンポは比の値域外＝そのまま通す
  return Number.isFinite(v) ? Math.min(1, Math.max(0, v)) : 1;
}

// 実時間再生の足内スケジュール＝各点の「足始端からの経過ミリ秒」。
//   secs（点ごとの市場時刻・秒）が点数と一致すれば実時刻どおりに配置する（実ティックの粗密が
//   そのまま実時間の粗密になる）。無い／不一致なら足を等分する（時刻を持たないモードでも
//   「1足＝時間足の長さ」は厳密に保つ）。[0, spanMs] へクランプする＝窓外の時刻（データ不整合）
//   で再生が止まらないようにする。
export function realtimeOffsetsMs({ n, secs, winStart, spanMs }) {
  const usable = Array.isArray(secs) && secs.length === n
    && secs.every((s) => Number.isFinite(Number(s)));
  const out = new Array(n);
  for (let i = 0; i < n; i++) {
    const raw = usable
      ? (Number(secs[i]) - winStart) * 1000
      : (n <= 1 ? 0 : spanMs * i / (n - 1));
    out[i] = Math.min(spanMs, Math.max(0, raw));
  }
  return out;
}

// 時間計算用（0除算/Infinity 回避。0保持はループ側ゲート）。（replay.js: effSpeed()）
export function effSpeed(s) {
  return Math.max(MIN_SPEED, s);
}

// 速度 s → フレーム間隔（1/s で減速・0は別途凍結）。（replay.js: frameMs()）
export function frameMs(s) {
  return BASE_FRAME_MS / effSpeed(s);
}

// 足内 1 ステップ間隔＝round(max(ANIM_MIN_MS,PER_POINT_MS)/effSpeed)。（replay.js: baseStepMs/effSpeed）
export function stepMs(s) {
  const baseStepMs = Math.max(ANIM_MIN_MS, PER_POINT_MS);
  return Math.round(baseStepMs / effSpeed(s));
}

// モード別の足内アニメ目安（s=1時）＝想定点数×PER_POINT_MS（密度反映）。（replay.js: animBaseMs()）
export function animBaseMs(mode) {
  if (mode === 'math') return 0;
  if (mode === 'open_only') return PER_POINT_MS;
  if (mode === 'ohlc_1min') return ANIM_COARSE * PER_POINT_MS;
  return ANIM_FINE * PER_POINT_MS; // real_ticks / every_tick 等（既定＝ANIM_FINE）
}

// 1足あたり所要のモデル推定＝計算(固定/実測) + (足内アニメ + 足送り間隔)/速度。（replay.js: estimatePeriodMs()）
export function estimatePeriodMs(lastComputeMs, mode, s) {
  const compute = (lastComputeMs == null) ? 50 : lastComputeMs;
  return compute + (animBaseMs(mode) + BASE_FRAME_MS) / effSpeed(s);
}

// 1足あたり実測所要の EMA（null=初回は dt、以降 prev*0.7 + dt*0.3）。（replay.js: emaPeriodMs 更新）
export function emaUpdate(prev, dt) {
  return (prev == null) ? dt : prev * 0.7 + dt * 0.3;
}

// ETA の 1足あたり所要＝実測(EMA)優先・無ければモデル推定。（replay.js: setEta() の period）
export function periodMs(emaPeriodMs, lastComputeMs, mode, s) {
  return (emaPeriodMs == null) ? estimatePeriodMs(lastComputeMs, mode, s) : emaPeriodMs;
}

// --- ISSUE-044: real_ticks（cap 廃止＝間引かない・絶対仕様）専用 ETA ------------------------------- //
//   参照実装（プロト replay.js）は real_ticks の cap 廃止（:436）時に ETA モデル（animBaseMs=
//   ANIM_FINE 前提 :277-282）を更新しておらず「cap 廃止後の正しい ETA」の定義が無い（月足×実ティックで
//   53 秒 vs 実測が桁違いに乖離）。依頼者承認（2026-07-06）に基づく拡張＝/candles の各足 tickvol
//   （実 tick 数）から算出する。以下 2 関数は参照実装からの抽出でなく本拡張の新規純関数。

// 残り足（現在 bar より後）の実 tick 総数。1 足でも tickvol 欠損/非有限があれば null
//   （＝呼び出し側は従来モデルへフォールバック。tickvol 無しデータセットの回帰なし）。
export function remainingTickvol(candles, bar) {
  let sum = 0;
  for (let i = bar + 1; i < candles.length; i++) {
    const v = candles[i] && candles[i].tickvol;
    if (v == null || !Number.isFinite(Number(v))) return null;
    sum += Number(v);
  }
  return sum;
}

// real_ticks の ETA＝実 tick 総数×ステップ間隔(stepMs) + 足あたり固定費(計算 compute + 足送り
//   BASE_FRAME_MS/effSpeed)×残り足数。compute は estimatePeriodMs と同じ既定（null→50ms・実測優先）。
export function etaRealTicksMs(tickvolSum, remainBars, lastComputeMs, s) {
  const compute = (lastComputeMs == null) ? 50 : lastComputeMs;
  return tickvolSum * stepMs(s) + remainBars * (compute + BASE_FRAME_MS / effSpeed(s));
}

// ETA の文字列整形（非有限/非正は「—」、60秒未満は「N秒」、60分未満は「M分SS秒」、以上は「H時間MM分」）。
//   （replay.js: fmtEta()。時間単位は ISSUE-044 追補＝real_ticks ETA 正確化で数時間規模が出るための
//   依頼者承認拡張（2026-07-06）。分/秒の既存書式は不変。）
export function fmtEta(ms) {
  if (!isFinite(ms) || ms <= 0) return '—';
  const s = Math.round(ms / 1000);
  if (s >= 3600) return `${Math.floor(s / 3600)}時間${String(Math.floor((s % 3600) / 60)).padStart(2, '0')}分`;
  return s >= 60 ? `${Math.floor(s / 60)}分${String(s % 60).padStart(2, '0')}秒` : `${s}秒`;
}
