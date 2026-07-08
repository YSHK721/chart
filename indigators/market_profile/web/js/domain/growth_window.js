// growth_window.js — GrowthWindow（domain 値・MP セッション/成長窓）。
//
// 設計入力: Model A 統一成長モデル Phase 3/4。表示モード（normal/sessions）× 時間足 tf × 因果カーソル
//   cursor から「MP セッション集計窓」を写像する単一の domain 値。現状 replay_market_profile_actor.js の
//   _buildFormingArgs に散在する 1D 決め打ち Math.floor(effNow/86400)*86400（当日始まり）を domain へ昇格し、
//   全時間足成長の核心＝この MP セッション窓のみを tf/mode パラメータ化する。両 app 共有（market_profile
//   モジュール所有）＝present（B 方式）と replay の双方が forCurrent を呼ぶ。
//
//   写像（forCurrent(mode,tf,cursor)→{from,to,formingStart}）:
//     - normal:   from=min(session_start,formingStart)（「絞った窓」＝日中足は当日始まり／上位足は当該バー期間）
//                 / to=cursor / formingStart=period_start(cursor,tf)。全期間累積だと 1 本ぶんの成長が極小で
//                 視認不能になるため当日を base 下限にする（ユーザー確定・視認性優先）。
//     - sessions: from=session_start(cursor)（暦日 anchor＝当日始まり）/ to=cursor /
//                 formingStart=period_start(cursor,tf)（backend forming_ticks の period_start_unix と同一 anchor）。
//     - 未知 mode（replay 等）: normal 扱い（絞った窓の安全側）。
//
//   1D=86400 の隔離: 暦日 anchor（session_start）は本 domain の sessionStart だけが 86400 を用いる。
//     bar-period anchor（period_start）は tf 依存で全時間足へ一般化する（1D も 86400 だが tf 表から導出）。
//
//   不変条件（未来リーク禁止・因果）: to<=cursor（to は cursor で確定＝等号成立）／formingStart<=to
//     （bar-period 始端は現在以下）。cursor=null（成長しない present 静止等）は窓を成さず {null,null,null}。
//
//   domain 値＝副作用なし・自内 import のみ（外側レイヤー非参照・Dependency Rule）。YAGNI: 明示 Port IF は
//     作らず、呼び出し側は静的メソッド forCurrent の戻り値（duck-typing）を読む。

const DAY = 86400; // 暦日 anchor（sessions のセッション境界）。1D=86400 の隔離点。

// tf → 足の秒長（bar-period 床の基準）。既存 market_profile_actor.TF_BAR_SEC・
//   replay 側 durationSecs と同一規約（未知/None は 1D=86400 相当へフォールバック）。
//   1W/1M は粗サブ解像度（ISSUE-030）＝暦周期の厳密床ではなく秒近似（front の窓幅算出用途に限る）。
const TF_BAR_SEC = {
  '1m': 60, '5m': 300, '15m': 900, '30m': 1800,
  '1h': 3600, '4h': 14400, '1D': 86400, '1W': 604800, '1M': 2592000,
};

export class GrowthWindow {
  // from: base 累積の下限（UNIX 秒・含む）。null=全期間（下限なし）。
  // to:   as-seen-at-t（現在の因果カーソル・UNIX 秒）。null=窓を成さない。
  // formingStart: bar-period forming の始端（UNIX 秒）。null=窓を成さない。
  constructor({ from = null, to = null, formingStart = null } = {}) {
    this._from = from;
    this._to = to;
    this._formingStart = formingStart;
  }

  get from() {
    return this._from;
  }

  get to() {
    return this._to;
  }

  get formingStart() {
    return this._formingStart;
  }

  // tf の足秒長（未知/None は 1D=86400 相当）。
  static barSec(tf) {
    const s = TF_BAR_SEC[tf];
    return Number.isFinite(s) && s > 0 ? s : DAY;
  }

  // bar-period 始端 = floor(cursor, tf)（tf 境界床）。backend forming_bar.period_start_unix と対応する
  //   front 側近似（固定周期 tf は同値。1W/1M は粗サブ解像度）。
  static periodStart(cursor, tf) {
    const s = GrowthWindow.barSec(tf);
    return Math.floor(cursor / s) * s;
  }

  // セッション始端 = 暦日 anchor = floor(cursor/86400)*86400（当日始まり）。1D=86400 の隔離点。
  static sessionStart(cursor) {
    return Math.floor(cursor / DAY) * DAY;
  }

  // 成長 push（bins モード）の表示 bin 幅ロック（ISSUE-047）: barw = 「from 直前の因果履歴レンジ / bins」。
  //   bins モードのままだと enterBar/growTo のたびに backend が binw=(累積窓レンジ/bins) を再導出し、
  //   レンジ拡大のたびにプロファイル全体（バー高さ・norm 正規化）が再スケールする。成長開始前の確定履歴
  //   （time<from の直近 ceil(86400/barSec(tf)) 本＝約 1 営業日ぶん・バー数基準ゆえ週末ギャップ非依存）の
  //   min(low)/max(high) レンジを「成熟時レンジの因果プロキシ」とし、barw を 1 回だけ導出して固定する
  //   （以降はレンジ拡大で bin 数のみ増える＝古典的 MP のティックサイズ固定と同型・未来リークなし）。
  //   ロック不能（履歴なし・レンジ縮退・from 欠損）は null を返し、呼び出し側は bins モードへフォールバック。
  static lockedBarw(candles, from, tf, bins) {
    const fromN = Number(from);
    if (from == null || !Number.isFinite(fromN) || !Array.isArray(candles)) {
      return null;
    }
    const binsN = Number(bins);
    const nb = Number.isFinite(binsN) && binsN > 0 ? binsN : 60;
    const nBack = Math.max(1, Math.ceil(DAY / GrowthWindow.barSec(tf)));
    // time<from（因果・確定履歴のみ）かつ low/high が有限な足の直近 nBack 本。
    const hist = candles.filter((c) => c && Number(c.time) < fromN
      && Number.isFinite(Number(c.low)) && Number.isFinite(Number(c.high))).slice(-nBack);
    if (hist.length === 0) {
      return null;
    }
    let lo = Infinity;
    let hi = -Infinity;
    for (const c of hist) {
      lo = Math.min(lo, Number(c.low));
      hi = Math.max(hi, Number(c.high));
    }
    const span = hi - lo;
    return span > 0 ? span / nb : null;
  }

  // 表示モード×tf×cursor から MP セッション集計窓を写像する（domain の単一源）。
  //   cursor 欠損（null/undefined/非有限）は窓を成さず {from:null,to:null,formingStart:null}。
  static forCurrent(mode, tf, cursor) {
    if (cursor == null || !Number.isFinite(Number(cursor))) {
      return new GrowthWindow({ from: null, to: null, formingStart: null });
    }
    const to = Number(cursor);
    const formingStart = GrowthWindow.periodStart(to, tf);
    // base 下限（from）:
    //   - sessions: 暦日 anchor（当日始まり）。
    //   - normal/未知: 「絞った窓」= min(当日始まり, formingStart)。全期間累積だと 1 本ぶんの成長が
    //     数年分に対して極小で視認不能になるため（ユーザー確定・視認性優先）、当日を base 下限にする。
    //     ただし 1W/1M は formingStart（週/月始端）が当日より前になるため min で formingStart 側へ寄せ、
    //     不変条件 from<=formingStart を保つ（＝日中足は当日／上位足は当該バー期間が窓）。
    const from = mode === 'sessions'
      ? GrowthWindow.sessionStart(to)
      : Math.min(GrowthWindow.sessionStart(to), formingStart);
    return new GrowthWindow({ from, to, formingStart });
  }
}
