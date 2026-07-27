// mp_fetch_params.js — 取得パラメータ（bins/va/src/range/resmode/period/dispbp）と
//   URL コンテキストへの写像ロール（ISSUE-181・SRP）。
//
// 設計入力（ISSUE-181）: MarketProfileActor は 6 アクター同居の神クラスで、その 1 つが
//   「URL パラメータ写像」（旧 market_profile_actor.js の setParams のホワイトリスト取り込み・
//   srcParam・_sessionsExtra・_periodExtra・_dispExtra）だった。変更要求の出所は
//   「UI の設定項目 → fetch コンテキスト（client が URL クエリへ写す）の対応規則」のみで、
//   表示モード遷移・リプレイ・tick 成長・チャートレイアウトとは独立している。
//
// 状態も一緒に移す（ISSUE-181 対応方針・参照実装 mp_primitive_roles.js の分割手法に倣う）:
//   取得パラメータ本体（旧 host._params）を本クラスが所有する。host は values() を読むだけで
//   フィールドを持たない（host 側の _params は本クラスへの読み取り専用アクセサに縮退）。
//
// host 契約（MpFetchParamsHost）が要求する最小メンバー（すべて read／呼び出し。代入しない）:
//   field : _sessions（日別モードか）/ _getCandles / _nowSec
//   method: _replayScrub.isReplay()（リプレイ中か。period 窓の抑止条件）
//
// 挙動不変: 受理キーのホワイトリスト・各 extra の分岐条件と戻り値（載せない＝空オブジェクト）は
//   抽出前と同一。URL byte 不変。

// セッション日境界（ISSUE-078・NY17:00 ET 基準）。日切り・当日窓・日別集計の唯一の規則源。
import { sessionDayStart } from '../../domain/session_day.js';
// ソース能力記述子（domain 単一情報源）: src 別の期間窓可否を導出する。
import { mpSourceCapability } from '../../domain/mp_source_capability.js';

// setParams が受理する取得パラメータのキー（これ以外は無視＝getContext/サーバ既定を潰さない）。
const ACCEPTED_KEYS = Object.freeze(['bins', 'va', 'src', 'range', 'resmode', 'period', 'dispbp']);

export class MpFetchParams {
  constructor(host) {
    this._host = host;
    // 取得パラメータ（bins/va/src/range）。set で更新し refresh 時に getContext へ重畳する。
    //   未設定時は空＝getContext のみ（サーバ既定・後方互換）。
    this._params = {};
  }

  // 現在の取得パラメータ（fetch context へ spread するための生オブジェクト）。
  values() {
    return this._params;
  }

  // 現在選択中の src（未設定は null）。composition root が tf-period 列への src 透過判定に使う。
  src() {
    return this._params.src ?? null;
  }

  // 取得パラメータを設定する。null/undefined のキーは無視する（getContext の値やサーバ既定を潰さない）。
  //   range（レンジpt）は client.buildMarketProfileUrl が barw へ写像する（'auto' は付与しない）。
  set(params = {}) {
    const next = {};
    for (const key of ACCEPTED_KEYS) {
      if (params[key] != null) {
        next[key] = params[key];
      }
    }
    this._params = next;
  }

  // sessions ON 時のみ context へ sessions:true を載せる（client が &sessions=1 を付与）。OFF は載せない（後方互換）。
  sessionsExtra() {
    return this._host._sessions ? { sessions: true } : {};
  }

  // 期間パラメータ（ISSUE-071 (b)案）: period='day' かつ zp かつ通常モードのとき、計測窓下限
  //   from=当日始端（最新ローソク time の属する UTC 日始端＝_sessionFrom の sessionStart と同規則）を
  //   fetch context へ載せる（client が &from= を付与し backend が candles を time>=from に限定）。
  //   それ以外（period 未設定/'all'・dwell・replay・sessions）は空＝従来 URL byte 不変（後方互換）。
  //   dwell を対象外にするのは、成長時の forming 経路が既に当日絞り（ISSUE-065）でありrefresh 窓まで
  //   絞ると static（ANALYSIS）の全期間表示という既存確定挙動を壊すため（zp は非増分＝refresh のみで安全）。
  periodExtra() {
    const host = this._host;
    if (this._params.period !== 'day' || !mpSourceCapability(this._params.src).hasPeriodWindow
        || host._sessions || host._replayScrub.isReplay()) {
      return {};
    }
    const candles = host._getCandles();
    const last = Array.isArray(candles) && candles.length ? candles[candles.length - 1] : null;
    let t = last && last.time != null ? Number(last.time) : NaN;
    if (!Number.isFinite(t)) {
      return {}; // ローソク未取得＝窓を成さず全期間へ縮退（既存 fetch と同じ非破壊）。
    }
    // ISSUE-086: 1W/1M バーの time はラベル（週末金曜/月末）＝未来日になり得るため、now で
    //   クランプして「現在のセッション日」へ正しく写像する（1m..1D はラベル=当日で不変）。
    const nowSec = host._nowSec();
    if (Number.isFinite(nowSec) && nowSec < t) {
      t = nowSec;
    }
    return { from: sessionDayStart(t) }; // ISSUE-078: セッション日始端。
  }

  // 表示幅(bp)→barw(pt) 写像（ISSUE-079 二層構造: 計算=1bp 固定・見せ方=自由）。
  //   dispbp 指定時、最新終値 close から barw = close × bp/1e4 を導出し、既存の resmode='range'
  //   ＋range(pt) 経路（client の &barw=・forming の base 整列・barw ロックまで全て再利用）へ写像する。
  //   backend 変更なしで時代整合（要求時の現在価格基準）を自動確保する。明示 resmode/range が
  //   ある場合（legacy 保存インスタンス）はそれを優先（後方互換）。ローソク未取得は写像せず
  //   サーバ既定（bins=60）へ縮退（非破壊）。
  dispExtra() {
    const bp = Number(this._params.dispbp);
    if (!(bp > 0) || this._params.resmode != null || this._params.range != null) {
      return {};
    }
    const candles = this._host._getCandles();
    const last = Array.isArray(candles) && candles.length ? candles[candles.length - 1] : null;
    const close = last && last.close != null ? Number(last.close) : NaN;
    if (!Number.isFinite(close) || !(close > 0)) {
      return {};
    }
    const barw = close * bp / 1e4;
    return { resmode: 'range', range: String(Math.round(barw * 10000) / 10000) };
  }
}
