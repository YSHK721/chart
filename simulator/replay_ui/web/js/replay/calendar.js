// replay/calendar.js — リプレイバーのカレンダー（再生開始日の選択）の純ロジック。
//
// DOM/fetch/timer 非依存。時刻は全て UTC で扱う（足の time が UNIX 秒＝UTC のため、
// ローカルタイムゾーンで日を切ると足の所属日とズレる）。日キーは "YYYY-MM-DD"。

const DAY_SECS = 86400;

const pad2 = (n) => (n < 10 ? `0${n}` : `${n}`);

// UNIX 秒 → 日キー "YYYY-MM-DD"（UTC）。
export function dayKey(unixSec) {
  const d = new Date(Math.floor(unixSec / DAY_SECS) * DAY_SECS * 1000);
  return `${d.getUTCFullYear()}-${pad2(d.getUTCMonth() + 1)}-${pad2(d.getUTCDate())}`;
}

// 日キー "YYYY-MM-DD" → その日の 00:00:00 UTC の UNIX 秒。
export function dayStartUnix(key) {
  const [y, m, d] = String(key).split('-').map((v) => parseInt(v, 10));
  return Date.UTC(y, m - 1, d) / 1000;
}

// {year, month(1-12)} を delta か月ずらす。
export function shiftMonth({ year, month }, delta) {
  const zero = year * 12 + (month - 1) + delta;
  return { year: Math.floor(zero / 12), month: (zero % 12 + 12) % 12 + 1 };
}

// 月グリッド（日曜始まり・6 週 = 42 セル固定）。前後月のセルは inMonth=false。
//   セル: { key, day, inMonth }。呼び出し側が days（存在する日の Set）で選択可否を決める。
export function monthCells({ year, month }) {
  const first = new Date(Date.UTC(year, month - 1, 1));
  const start = first.getTime() / 1000 - first.getUTCDay() * DAY_SECS; // 直前の日曜へ戻す
  const cells = [];
  for (let i = 0; i < 42; i++) {
    const t = start + i * DAY_SECS;
    const d = new Date(t * 1000);
    cells.push({
      key: dayKey(t),
      day: d.getUTCDate(),
      inMonth: d.getUTCFullYear() === year && d.getUTCMonth() + 1 === month,
    });
  }
  return cells;
}

// days（"YYYY-MM-DD" の昇順 list）の末尾の日が属する月＝カレンダー初期表示月。
//   空なら null（呼び出し側は開かない／当月を出す等の判断をする）。
export function latestMonth(days) {
  if (!days || !days.length) return null;
  const [y, m] = String(days[days.length - 1]).split('-').map((v) => parseInt(v, 10));
  return { year: y, month: m };
}
