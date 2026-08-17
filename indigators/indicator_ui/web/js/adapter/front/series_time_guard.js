// SeriesTimeGuard（adapter/front/series_time_guard.js）— 系列データの時系列契約防壁（単一ソース）。
//
// ISSUE-383: lightweight-charts は系列データに「厳密増加する time」を要求するが、vendor の
//   production ビルドはこの検証を持たない（検証はデバッグビルド限定）。契約違反の配列を setData に
//   通すと内部の index↔行 二分探索が破綻し、以後クロスヘア/ペイントのたびに `Value is null` を
//   throw し続ける回復不能状態になる（最小再現で実測: 逆行 1 点の混入で恒久再発・リロードまで継続。
//   スタック: a ← xt.Line[Mh] ← xt.Sh ← Ce.DM/TM）。例外は後続の rAF・mousemove で飛ぶため
//   呼出側 try/catch では捕捉できず、full 再計算バッチも同じ throw で中断→固着する。
//
// ローソク系列は ISSUE-167 で同じ防壁（candle_feed.dedupeCandlesByTime）を得ているが、指標系列
//   （line/histogram/level_dash）には防壁が無かった。本モジュールはそれを全系列へ一般化する。
//
// 規約（dedupeCandlesByTime と同一の畳み方＋発生源特定のための可視化を追加）:
//   - 清浄（数値 time が厳密増加）なら**同一参照**を返す＝挙動 byte 不変・追加アロケーションなし。
//   - 違反（同 time / 後退 time）を検出したら console.error でフィンガープリント（系列ラベル・
//     違反位置・前後の time・畳み前後の点数）を出し、同 time は後勝ち（keep-last）・後退は捨てて
//     厳密増加へ畳んだ複製を返す。ログは発生源（どの系列がいつ契約違反データを生んだか）の
//     特定材料であり、握り潰しではない（発生源修正と多重防御の二段構え＝ISSUE-167 と同じ裁定）。
//   - 比較は両者の time が数値のときのみ行う（business day 等の非数値表現は candle 防壁と同様に
//     対象外＝従来挙動のまま通す）。

// 能動通知（ユーザー裁定 2026-08-17）: console.error は DevTools を開かない限り視認されず、
//   発生源特定（ISSUE-383 残調査）の入口が受動監視のみになる欠陥があった。違反検出時に呼ぶ
//   通知先（トースト等）を composition 側が登録する。本モジュールは View を知らない（SRP・
//   依存方向は composition → guard の一方向）。通知失敗は握る（診断の失敗で描画を壊さない）。
let _notifier = null;

export function setSeriesTimeGuardNotifier(fn) {
  _notifier = (typeof fn === 'function') ? fn : null;
}

// 最初の違反位置を返す（清浄なら -1）。O(n)・アロケーションなし。
export function findTimeOrderViolation(points) {
  if (!Array.isArray(points)) {
    return -1;
  }
  for (let i = 1; i < points.length; i++) {
    const a = points[i - 1]?.time;
    const b = points[i]?.time;
    if (typeof a === 'number' && typeof b === 'number' && b <= a) {
      return i;
    }
  }
  return -1;
}

// 防壁本体。清浄なら同一参照を返す。違反時はフィンガープリントを console.error し、
//   keep-last / 後退捨てで厳密増加へ畳んだ複製を返す。
export function enforceAscendingTimes(points, label) {
  const at = findTimeOrderViolation(points);
  if (at < 0) {
    return points;
  }
  const out = [];
  for (const p of points) {
    const n = out.length;
    if (n > 0 && typeof p?.time === 'number' && typeof out[n - 1].time === 'number') {
      if (p.time === out[n - 1].time) { out[n - 1] = p; continue; } // 同 time は後勝ち（keep-last）
      if (p.time < out[n - 1].time) { continue; } // 後退（想定外）は捨て厳密増加を維持
    }
    out.push(p);
  }
  const fingerprint = {
    firstViolationIndex: at,
    prevTime: points[at - 1]?.time,
    time: points[at]?.time,
    before: points.length,
    after: out.length,
  };
  if (typeof console !== 'undefined' && console.error) {
    // フィンガープリントは文字列にも埋め込む（op_log は追加引数のオブジェクトを "[object Object]" に
    //   潰すため、リロード後の __opsPrev() 回収でも違反位置・時刻が残るようにする＝ISSUE-383 残調査の
    //   入力をログ単体で完結させる）。オブジェクト引数は DevTools での展開閲覧用に併置する。
    console.error(
      `[series-time-guard] 時系列契約違反を検出・畳み込み（発生源調査用）: ${label ?? '(no label)'} `
      + JSON.stringify(fingerprint),
      fingerprint,
    );
  }
  if (_notifier) {
    try {
      _notifier(label ?? '(no label)');
    } catch (_e) {
      // 通知（診断の可視化）の失敗で防壁・描画本体を壊さない。証跡は上の console.error が残す。
    }
  }
  return out;
}
