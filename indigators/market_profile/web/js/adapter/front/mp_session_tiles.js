// mp_session_tiles.js — 日別（sessions）タイル反映＋初回オートズームのロール（ISSUE-181・SRP）。
//
// 設計入力（ISSUE-181）: MarketProfileActor は 6 アクター同居の神クラスで、その 1 つが
//   「日別タイル＋自動ズーム」（旧 market_profile_actor.js の _applySessions / _focusSessionsPending /
//   SESSIONS_INITIAL_SPAN_SEC と表示組立の純変換 _buildSessionView / _sessionDateToUnix）だった。
//   変更要求の出所は「日別プロファイルをタイルとして描くか（tf-period 列へ委ねるか）・読み取り欄へ
//   当日 MP を供給するか・ローソクを透明化するか・初回だけ被覆日レンジへ寄せるか」のみで、
//   表示モード遷移・取得パラメータ写像・tick 逐次成長・リプレイ操作系・チャートレイアウトとは独立している。
//
// 状態も一緒に移す（ISSUE-181 対応方針・参照実装 mp_primitive_roles.js の分割手法に倣う）:
//   初回オートズームの pending フラグ（_sessionsFocusPending）を本クラスが所有する。
//   host はこのフィールドを own field として持たない（_markSessionsFocusPending 経由で立てる）。
//
// host 契約（MpSessionTilesHost）が要求する最小メンバー（すべて read／呼び出し。代入しない）:
//   field : _sessions（日別モードか）/ _sessionsDrawnByTfPeriod（tf-period 列が描くかの述語）/
//           _primitive（setSessions）/ _renderer（setSessionMP / setCandleTransparency / focusTimeRange）/
//           _getCandles
//
// ★ ISSUE-164（ビュー自動介入の全廃・ユーザー裁定）に関する不変条件 ★
//   本抽出は「どのオブジェクトが状態を持つか」だけを変える。ビュー操作（focusTimeRange）の
//   呼び出し箇所・ガード・条件分岐・発火順序は抽出前と 1 行も変えていない:
//     - focusTimeRange の呼び出しは従来どおり 2 箇所（applySessions の pending 経路 /
//       focusPending の tfDraws 経路）のみ。増やしても減らしてもいない。
//     - pending は「非 sessions → sessions の新規入場」でのみ立ち（立てるのは MpModeTransition）、
//       1 回消費したら false に落ちる。off 遷移でクリアする。
//     - 自動補正の同梱・ガードの隠蔽は一切行わない。

// セッション日 OHLC 集計（domain 純関数・ISSUE-094 V6 抽出）。集計数学を adapter から分離。
import { aggregateSessionOhlc } from '../../domain/session_ohlc.js';

// 日別（sessions）初回オートズームの最大遡り期間（直近1年）。ISSUE-055: 全期間（1D で最大3.6年）を初回に
//   映すと tf-period 列が可視域ぶん一括取得され応答肥大（実測 87MB・warm でも数秒）で初回表示が重い。初回は
//   直近1年に限定し（古い範囲はスクロールで＝A案デバウンス＋per-day キャッシュで滑らか）、初回取得量/描画を抑える。
//   データが1年未満（intraday 等）のときは実効的に全期間（下限＝最古足）で不変。
const SESSIONS_INITIAL_SPAN_SEC = 365 * 86400;

// sessions の 'YYYY-MM-DD' → UNIX 秒（UTC 深夜）。candle.time との突合に使う（primitive dateToUnix と同一規則）。
function _sessionDateToUnix(dateStr) {
  const parts = String(dateStr).split('-');
  const y = Number(parts[0]);
  const m = Number(parts[1]);
  const d = Number(parts[2]);
  return (y > 0 && m > 0 && d > 0) ? Date.UTC(y, m - 1, d) / 1000 : NaN;
}

// sessions 応答を表示用に組み立てる純変換（SRP: ロールは制御に留め、変換は本関数へ）。
//   ① 各セッションへ当日 candle の OHLC を **セッション日集計**で付与（列内 OHLC 描画用・ISSUE-078:
//      日曜夜 UTC の足も月曜セッションへ束ねる）。1D は日=バー 1:1 で date→time 突合と同値。
//      日中足（1m 等）は当日全バーの集計 OHLC になる（ISSUE-072 の日集計を セッション日へ一般化）。
//   ② 当日の実在バー範囲 tFirst/tLast（当日最初/最後のバー time）を付与する。primitive の日別タイルが
//      日中足で「日の実在バー範囲」へ整列するための時間軸アンカー（深夜 00:00 バー不在でも解決できる）。
//   ③ 当日 MP（POC/VAH/VAL）の time→mp Map を作る。VA/POC は backend が _value_area 単一定義で算出済み
//      （poc/va_low/va_high）＝frontend は表示に写すだけ（DRY・VA 定義は backend に一元化）。
//   戻り値 { list, mp }。list=OHLC/tFirst/tLast 付与済みセッション配列、mp=time→{poc,vah,val} の Map。
function _buildSessionView(list, candles) {
  // セッション日ラベル → { tFirst, tLast, open, high, low, close }（当日全バーの範囲と日次 OHLC）。
  //   集計数学は domain/session_ohlc.js（純関数）へ外出しした（ISSUE-094 V6）。ここは表示組立に専念する。
  const byDay = aggregateSessionOhlc(candles);
  const out = [];
  const mp = new Map();
  for (const s of list) {
    const t = _sessionDateToUnix(s.date);
    const agg = byDay.get(String(s.date)); // ラベル同士で突合（backend sessions ラベルもセッション日）。
    out.push(agg ? {
      ...s,
      open: agg.open, high: agg.high, low: agg.low, close: agg.close,
      tFirst: agg.tFirst, tLast: agg.tLast,
    } : s);
    if (s.poc != null && s.va_low != null && s.va_high != null && Number.isFinite(t)) {
      mp.set(t, { poc: s.poc, vah: s.va_high, val: s.va_low });
    }
  }
  return { list: out, mp };
}

export class MpSessionTiles {
  constructor(host) {
    this._host = host;
    // 日別（sessions）初回オートズームの pending。true のとき次の applySessions / focusPending が
    //   1 回だけ被覆日レンジへ寄せて false に落とす。立てるのは MpModeTransition（新規入場時のみ）。
    this._sessionsFocusPending = false;
  }

  // 日別（sessions）初回オートズームを pending にする（MpModeTransition が新規入場でのみ呼ぶ）。
  markFocusPending() {
    this._sessionsFocusPending = true;
  }

  // sessions（日別プロファイル分割）を primitive/renderer へ反映する。
  //   on: primitive.setSessions(profile.sessions)・renderer.setCandleTransparency(true)（ローソク透明化）。
  //   off: primitive.setSessions(null)（通常モード）・renderer.setCandleTransparency(false)（復元）。
  //   該当メソッド非提供時は skip（後方互換）。移植元 prototype_260630-01 drawSessions。
  applySessions(profile) {
    const host = this._host;
    const on = !!host._sessions;
    // tf-period 列が日別プロファイルを描くモード（player tf の日別）か。true のとき本ロールは日別タイルを
    //   描かず（先に届く sessions 応答での一瞬のタイル描画→tf-period 列への差し替えちらつきを防ぐ）、candle
    //   透明化も tf-period 側（列描画時）へ委ねる（それまで candle 可視＝空白回避）。ISSUE-055。
    const tfDraws = on && host._sessionsDrawnByTfPeriod();
    // 表示用ビュー（OHLC 付与済みリスト＋当日 MP Map）を純変換で一括構築する（SRP）。読取欄は tfDraws でも要る。
    const rawList = on && profile && Array.isArray(profile.sessions) ? profile.sessions : null;
    const view = (rawList && rawList.length)
      ? _buildSessionView(rawList, (typeof host._getCandles === 'function' ? host._getCandles() : []))
      : null;
    if (host._primitive && typeof host._primitive.setSessions === 'function') {
      // tfDraws のときは日別タイルを描かない（tf-period 列が描く）＝setSessions(null)。
      host._primitive.setSessions(tfDraws ? null : (view ? view.list : null));
    }
    // 読み取り欄: クロスヘアが当日を指したとき OHLC に加え当日 MP（POC/VAH/VAL）を出す（sessions のみ・tfDraws でも供給）。
    if (host._renderer && typeof host._renderer.setSessionMP === 'function') {
      host._renderer.setSessionMP(view ? view.mp : null);
    }
    // candle 透明化: tfDraws のときはここで触らず tf-period 側（列描画時に true / 無効化時に false）へ委ねる。
    //   非 tfDraws（通常の日別タイル or OFF）は従来どおり on で透明化/復元する。
    if (!tfDraws && host._renderer && typeof host._renderer.setCandleTransparency === 'function') {
      host._renderer.setCandleTransparency(on);
    }
    // sessions を有効化した初回のみ、被覆セッション日の時間レンジへズームを寄せる（時間軸連動タイルが
    //   潰れない／短周期でも日別列が画面内に入るように）。以後は寄せない（手動ズーム/スクロール尊重）。
    //   時間ベース（focusTimeRange）にすることで、1m（1日=1440本）でも「日数」を「バー数」と誤解して
    //   列が画面外に落ちる不具合を解消する。off 遷移でフラグをクリア。
    if (on) {
      if (this._sessionsFocusPending && rawList && rawList.length
          && host._renderer && typeof host._renderer.focusTimeRange === 'function') {
        const candles = (typeof host._getCandles === 'function' ? host._getCandles() : []) || [];
        if (candles.length) {
          this._sessionsFocusPending = false;
          // 被覆日の下限＝最古セッション日始端（ただしロード済み candle の左端より前へは行かない＝空白回避）。
          const sessStart = _sessionDateToUnix(rawList[0].date);
          const oldest = Number.isFinite(sessStart)
            ? Math.max(sessStart, candles[0].time)
            : candles[0].time;
          const to = candles[candles.length - 1].time; // 右端＝最新足（now）。
          // 初回は直近 SESSIONS_INITIAL_SPAN_SEC（1年）に限定する（初回 tf-period 取得量/描画負荷の抑制・
          //   ISSUE-055）。1年未満のデータでは oldest が下限となり実効全期間で不変。
          const from = Math.max(oldest, to - SESSIONS_INITIAL_SPAN_SEC);
          host._renderer.focusTimeRange(from, to);
        }
      }
    } else {
      this._sessionsFocusPending = false;
    }
  }

  // ISSUE-067: 日別 focus を candle 範囲だけで行う（sessions フェッチをスキップする tfDraws 経路用）。
  //   applySessions の focus ブロックと同一規則（初回のみ・直近 SESSIONS_INITIAL_SPAN_SEC＝1年に限定・
  //   candle 左端より前へ行かない）。sessStart（セッション応答由来の下限引き上げ）は使わない＝candle 左端が下限。
  focusPending() {
    const host = this._host;
    if (!this._sessionsFocusPending || !host._renderer
        || typeof host._renderer.focusTimeRange !== 'function') {
      return;
    }
    const candles = (typeof host._getCandles === 'function' ? host._getCandles() : []) || [];
    if (!candles.length) {
      return;
    }
    this._sessionsFocusPending = false;
    const to = candles[candles.length - 1].time;
    const from = Math.max(candles[0].time, to - SESSIONS_INITIAL_SPAN_SEC);
    host._renderer.focusTimeRange(from, to);
  }
}
