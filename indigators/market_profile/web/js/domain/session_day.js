// session_day.js — セッション日境界の単一定義（frontend 版・ISSUE-078・NY17:00 ET 基準）。
//
// backend marketdata/session_day.py と同一規則の JS 実装（規則の二重「定義」を避けるため、
//   両者とも IANA tz 'America/New_York' を唯一の DST 権威にする＝自前カレンダー禁止。JS 側は
//   Intl.DateTimeFormat がブラウザ/node 内蔵の tz データベースを引く）。
//
// 定義: セッション日＝ブローカー時間（NY + 7h＝NY17:00 が 00:00）の暦日。境界（始端）は
//   「NY ローカル前日 17:00」＝夏 21:00 UTC / 冬 22:00 UTC。DST 切替日は 23h/25h セッション。
//   1D バーの time 表示規約はラベル日の UTC 深夜（sessionBarTime＝backend session_bar_time と同値）。
//
// 実装注意: NY 17:00 は DST 切替時刻（02:00）と重ならないため曖昧・不存在時刻は生じない。
//   逆変換（NY 壁時計→UTC）は 2 パスのオフセット解決で確定する（1 パス目の推定オフセットで
//   候補 UTC を作り、その時点のオフセットで再解決＝17:00 固定ゆえ収束）。

// hourCycle 'h23' で 24:00 表記を避ける（'2-digit'+hour12:false は環境により '24' を返す）。
const _NY_FMT = new Intl.DateTimeFormat('en-US', {
  timeZone: 'America/New_York',
  year: 'numeric', month: '2-digit', day: '2-digit',
  hour: '2-digit', minute: '2-digit', second: '2-digit', hourCycle: 'h23',
});

// t（UNIX 秒）→ NY 壁時計を「擬似 UTC epoch 秒」で返す（Date.UTC(NY 各成分)）。
function _nyWallAsUtc(tSec) {
  const parts = _NY_FMT.formatToParts(new Date(tSec * 1000));
  const v = {};
  for (const p of parts) {
    if (p.type !== 'literal') {
      v[p.type] = Number(p.value);
    }
  }
  return Date.UTC(v.year, v.month - 1, v.day, v.hour % 24, v.minute, v.second) / 1000;
}

// t 時点の NY オフセット秒（EDT=-14400 / EST=-18000。nyWall = t + offset）。
function _nyOffset(tSec) {
  return _nyWallAsUtc(tSec) - tSec;
}

// NY 壁時計（擬似 UTC epoch 秒）→ 実 UTC 秒（2 パスのオフセット解決・17:00 は非曖昧で収束）。
function _utcFromNyWall(nyWallSec) {
  const guess = nyWallSec - _nyOffset(nyWallSec); // 1 パス目: 壁時計値をそのままオフセット推定に使う。
  return nyWallSec - _nyOffset(guess);            // 2 パス目: 候補時点のオフセットで確定。
}

// t が属するブローカー暦日の「擬似 UTC 深夜 epoch 秒」（＝sessionBarTime の値そのもの）。
function _brokerMidnight(tSec) {
  const brokerSec = _nyWallAsUtc(tSec) + 7 * 3600; // ブローカー時間 = NY + 7h。
  return Math.floor(brokerSec / 86400) * 86400;
}

// t（UNIX 秒）が属するセッション日の始端 UNIX 秒（境界ちょうどは新セッション）。
export function sessionDayStart(t) {
  return _utcFromNyWall(_brokerMidnight(t) - 7 * 3600); // 始端 = NY ローカル（ラベル前日）17:00。
}

// t が属するセッション日の翌セッション始端（半開区間の終端）。DST 切替日は start+86400 と異なる。
export function nextSessionDayStart(t) {
  return _utcFromNyWall(_brokerMidnight(t) + 86400 - 7 * 3600);
}

// t が属するセッション日のラベル 'YYYY-MM-DD'（ブローカー暦日）。
export function sessionDateLabel(t) {
  return new Date(_brokerMidnight(t) * 1000).toISOString().slice(0, 10);
}

// 1D バーの time 表示規約値＝セッション日ラベルの UTC 深夜 epoch（backend session_bar_time と同値）。
export function sessionBarTime(t) {
  return _brokerMidnight(t);
}

// ラベル 'YYYY-MM-DD' → 当該セッション日の始端 UNIX 秒（sessionDateLabel の逆）。不正は NaN。
export function sessionLabelToStart(label) {
  const parts = String(label).split('-');
  const y = Number(parts[0]);
  const m = Number(parts[1]);
  const d = Number(parts[2]);
  if (!(y > 0) || !(m > 0) || !(d > 0)) {
    return NaN;
  }
  return _utcFromNyWall(Date.UTC(y, m - 1, d) / 1000 - 7 * 3600);
}
