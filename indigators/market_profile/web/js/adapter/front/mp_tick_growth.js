// mp_tick_growth.js — tick 逐次成長（forming/accumulator による足内 pull 成長）ロール（ISSUE-181・SRP）。
//
// 設計入力（ISSUE-181）: MarketProfileActor は 6 アクター同居の神クラスで、その 1 つが
//   「tick 逐次成長」（旧 market_profile_actor.js の _isIncremental / applyGrowthState /
//   onLiveTick / _enterTicklive / _exitTicklive と presence ガード _hasBaseFields）だった。
//   変更要求の出所は「ライブ tick 契機で現在形成足を足内成長させる規則（base 取得・尾部 since・
//   rollover 検出・空 profile の非破壊ガード）」のみで、表示モード遷移・日別タイル・
//   リプレイ・スクラブ・チャートレイアウト・URL パラメータ写像とは独立している。
//
// 状態も一緒に移す（ISSUE-181 対応方針・参照実装 mp_primitive_roles.js の分割手法に倣う）:
//   成長状態（_growing）・現在の DwellAccumulator（_accumulator）・現在足の formingStart
//   （_formingStart）・最後に addTick した tick 秒（_lastSec）・注入依存（_formingClient /
//   _makeAccumulator）を本クラスが所有する。host はこれらを own field として持たない。
//
//   ただし host（MarketProfileActor）側には prototype アクセサ（getter/setter）を残す:
//   replay subclass（simulator/replay_ui/.../replay_market_profile_actor.js の push 戦略）が
//   `a._accumulator = acc` / `a._formingStart = ...` / `a._lastSec = ...` / `a._formingClient` /
//   `a._makeAccumulator()` で actor の当該フィールド面を直接読み書きしているため、その面を
//   1 バイトも変えずに維持する（ISSUE-145 の INTRABAR_FORMING_IDS 登録＝足内 tick 更新の
//   駆動経路もこの面の上に成立しているため、読み書き面の変更は禁止）。
//
// host 契約（MpTickGrowthHost）が要求する最小メンバー（すべて read／呼び出し。代入しない）:
//   field : _enabled（MP 有効フラグ）/ _sessions（日別モードか）/ _params（src 能力判定）/
//           _primitive（setProfile）
//   method: refresh（非増分時の byte-identical 委譲先。replay subclass が override するため host 経由）
//           _isIncremental / _enterTicklive / _buildFormingArgs（いずれも subclass override を
//           尊重するため host 経由でディスパッチする＝抽出前の `this.` 呼び出しと同一の仮想結合）
//
// 挙動不変（ISSUE-181 の目的）: ガードの評価順序・await の位置・rollover 判定・fold 手順・
//   非破壊フォールバック（null / 必須フィールド欠損は前回描画を保持）は抽出前と同一。
//   ビュー（ズーム・スクロール・可視レンジ）へは一切介入しない（ISSUE-164 裁定）。

// ソース能力記述子（domain 単一情報源）: src 別の増分可否を導出する（ISSUE-097 🟡-9）。
import { mpSourceCapability } from '../../domain/mp_source_capability.js';

// MP-05: ticklive base=1 応答が DwellAccumulator.init を NaN 汚染せず駆動できるかの presence ガード。
//   無ローソク等で空 profile が返ると priceMin/priceMax/nBins/gridW が欠損し、init の binw/kw0 が NaN と
//   なり snapshot が NaN 価格を出す。必須フィールド（レンジ/グリッド/base 配列）がすべて有限/配列のときだけ
//   true を返し、欠損時は呼び出し側で null 扱い（増分に入らず前回描画を保持＝既存 fetch null と同じ非破壊）。
//   baseKmin は init が priceMin/gridW から導出フォールバックするため必須に含めない。
//   注意: JSON の明示 null は Number(null)===0（有限）で誤通過するため、各必須数値は `!= null`（null/
//   undefined 双方を除外）を先に課してから有限性を判定する（欠損 = 増分に入れない）。
function _finiteNum(x) {
  return x != null && Number.isFinite(Number(x));
}
function _hasBaseFields(f) {
  return !!f
    && _finiteNum(f.priceMin)
    && _finiteNum(f.priceMax)
    && _finiteNum(f.nBins) && Number(f.nBins) > 0
    && _finiteNum(f.gridW) && Number(f.gridW) > 0
    // ISSUE-260: VA 比率はサーバ解決値（応答 vaPct）に従う。front は既定リテラルを持たないため、
    //   欠損時は増分に入らず前回描画を保持する（自前の既定へ黙って落ちない＝不一致を作らない）。
    && _finiteNum(f.vaPct) && Number(f.vaPct) > 0
    && Array.isArray(f.baseFine);
}

export class MpTickGrowth {
  constructor(host, { formingClient, makeAccumulator } = {}) {
    this._host = host;
    // tick 逐次成長（ticklive・増分2 系とは独立の 4 つ目の排他モード）。未注入時は非増分（refresh 委譲）。
    this._formingClient = formingClient ?? null;
    this._makeAccumulator = typeof makeAccumulator === 'function' ? makeAccumulator : null;
    // Model A 直交化: 成長状態（growing/static）。成長エンジン（isIncremental/onLiveTick/enter）は
    // この _growing で駆動する（表示モードと成長状態の分離）。Phase1 は mode='ticklive' が唯一
    // _growing=true を立てる互換維持（_ticklive とロックステップ＝挙動不変）。Phase2 で mode 非依存の
    // applyGrowthState が直接トグルできるようにする（FOLLOW+normal 成長など）。
    this._growing = false;
    this._accumulator = null;     // 現在の DwellAccumulator（null＝未 enter）。
    this._formingStart = null;    // 現在足の formingStart（rollover 検出用）。
    this._lastSec = null;         // 最後に addTick した tick 秒（base=0 尾部 since）。
  }

  // ---- 状態アクセス（host の prototype アクセサが委譲する読み書き面）----
  growing() { return this._growing; }

  setGrowing(value) { this._growing = value; }

  accumulator() { return this._accumulator; }

  setAccumulator(value) { this._accumulator = value; }

  formingStart() { return this._formingStart; }

  setFormingStart(value) { this._formingStart = value; }

  lastSec() { return this._lastSec; }

  setLastSec(value) { this._lastSec = value; }

  formingClient() { return this._formingClient; }

  accumulatorFactory() { return this._makeAccumulator; }

  // 増分（forming/accumulator）成長が可能か: growing かつ formingClient・accumulator factory 注入済み、
  //   かつ **sessions モードでない**こと。いずれか欠ければ非増分（onLiveTick は refresh へ委譲＝回帰ゼロ）。
  //   Model A Phase3（成長経路の分岐）: sessions+growing は forming 単一プロファイル（enter→setProfile）を
  //   sessions 描画へ被せず、refresh(to=cursor, sessions=1) で backend の因果 sessions 分割（当日=
  //   [session_start,to)・過去日静的）を取得する（review🔵4 の破綻状態を正しく解消）。よって _sessions 時は
  //   非増分＝refresh 経路へ倒す（accumulator は sessions で使わない＝共有グリッド不整合回避）。
  //   normal/replay+growing は従来どおり増分（全期間 base + bar-period forming）。
  //   src=zp（超過占有 z(p)）は per-tick 増分が定義できない（帰無モーメント込みの再計算が必要）ため
  //   非増分＝refresh 委譲へ倒す（onLiveTick はライブ足更新周期＝数秒に 1 回。backend は当日 null を
  //   経過分キーでメモし 0.05〜0.2s 程度で応答する）。
  isIncremental() {
    const host = this._host;
    return !!this._growing && !host._sessions && mpSourceCapability(host._params.src).incremental
      && !!this._formingClient && !!this._makeAccumulator;
  }

  // Model A 直交化: 成長状態を表示モードと独立に設定する単一信号（境界追加・ロジックは不変）。
  //   growing=true で成長エンジン（isIncremental→onLiveTick/enter/forming）を有効化する。
  //   これにより mode を維持したまま（例: FOLLOW+normal）成長 ON/OFF を切替えられる（present #2 の直交化）。
  //   growing=false へ遷移する際は成長エンジンの累積器/尾部を破棄する（static 復帰＝enter 再入の初期化）。
  applyState({ growing } = {}) {
    const next = !!growing;
    if (next === !!this._growing) {
      return; // 同状態は no-op（冪等）。
    }
    this._growing = next;
    if (!next) {
      // static 復帰: 累積器/形成足/尾部を破棄（次回 growing=true で enter が再取得・再 init）。
      this._accumulator = null;
      this._formingStart = null;
      this._lastSec = null;
    }
  }

  // ライブ tick 契機。増分（ticklive）: 未 enter なら enter、以降は base=0 尾部を addTick して
  //   snapshot を反映。formingStart 変化（rollover）で enter を再実行。
  //   非増分: host.refresh() へ byte-identical 委譲（ticklive OFF / formingClient 未注入＝回帰ゼロ）。
  //   分岐判定・再入は host 経由でディスパッチする（subclass の override を尊重＝抽出前と同一）。
  async onLiveTick() {
    const host = this._host;
    if (!host._isIncremental()) {
      return host.refresh(); // 非増分＝既存 refresh と同一（後方互換・回帰ゼロ）。
    }
    if (!host._enabled) {
      return undefined;
    }
    if (!this._accumulator) {
      return host._enterTicklive(); // 初回＝UC-01。
    }
    const forming = await this._formingClient.fetchForming(
      host._buildFormingArgs({ base: 0, since: this._lastSec }),
    );
    if (!forming) {
      return undefined; // null は前回描画を保持（非破壊）。
    }
    if (forming.formingStart !== this._formingStart) {
      return host._enterTicklive(); // rollover: base を取り直して reset。
    }
    for (const t of forming.ticks) {
      this._accumulator.addTick(t[0], t[1]);
      this._lastSec = t[0];
    }
    host._primitive.setProfile(this._accumulator.snapshot());
    return undefined;
  }

  // UC-01: base=1 を取得して accumulator を init、forming tick 列を畳み込み、snapshot を描画する。
  async enter() {
    const host = this._host;
    if (!host._enabled || !host._isIncremental()) {
      return;
    }
    const forming = await this._formingClient.fetchForming(
      host._buildFormingArgs({ base: 1, since: null }),
    );
    if (!forming) {
      return; // null は前回描画を保持（非破壊）。
    }
    // MP-05 是正: base=1 応答の必須フィールド（レンジ/グリッド/base 配列）が欠損（無ローソク等の空
    //   profile）なら init へ NaN が伝播し snapshot が NaN 価格を出す。presence ガードで欠損時は null と
    //   同じ扱い（増分に入らず前回描画を保持＝既存 fetch null と同じ非破壊挙動）にする。
    if (!_hasBaseFields(forming)) {
      return; // 空 profile（必須フィールド欠損）は前回描画を保持（非破壊・NaN 混入を防ぐ）。
    }
    const acc = this._makeAccumulator();
    acc.init({
      baseFine: forming.baseFine,
      baseKmin: forming.baseKmin,
      activeTable: forming.activeTable,
      priceMin: forming.priceMin,
      priceMax: forming.priceMax,
      nBins: forming.nBins,
      gridW: forming.gridW,
      formingStart: forming.formingStart,
      vaPct: forming.vaPct, // ISSUE-260: サーバ解決済み VA 比率（参照実装と同一値）。
    });
    this._accumulator = acc;
    this._formingStart = forming.formingStart;
    this._lastSec = null;
    for (const t of forming.ticks) {
      acc.addTick(t[0], t[1]);
      this._lastSec = t[0];
    }
    host._primitive.setProfile(acc.snapshot());
  }

  // 成長を解除する（累積器破棄・通常経路復帰・冪等）。表示モード側の ticklive フラグ解除は host が担う。
  exit() {
    this._growing = false;  // モード離脱＝成長 OFF（Phase1 互換: _ticklive とロックステップ）。
    this._accumulator = null;
    this._formingStart = null;
    this._lastSec = null;
  }
}
