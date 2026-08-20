// position_sizing_controller.js — 計算機の協働子（ISSUE-368 スライス 7）。
//
// 設計入力: .doc/POSITION_SIZING_CHART_INTEGRATION_DESIGN.md §5（Input Boundary＝usecase の
//   setLevels / setParams / runMonteCarlo）・§6（表示先はモーダルと水準線 primitive の 2 つ）・
//   スライス 7（協働子は共有配線が生成し、root は識別子の受け渡しのみ）。
//
// 責務（SRP）: **繋ぐだけ**。式・判定・不変条件は 1 つも持たない（ColorThemeController と同じ位置）。
//   状態も持たない: 現在の水準（E-02）の保持者は usecase 1 か所で、本 class は取り次ぐだけ。
//   - 入力（モーダル・右クリック・ピッカー）を usecase の Input Boundary へ写す
//   - usecase の ViewModel を 2 つの表示先（モーダル／水準線 primitive）へ配る
//   - 価格の**書き戻し経路は 1 本**（モーダルの入力欄）。右クリックもピッカーもここを通るため、
//     「チャートから入れた値」と「手で打った値」で状態が割れない
//
// 依存: usecase（内向き）と注入された協働子だけ。lwc・DOM・fetch は触らない。

import { createPriceLevels } from '../../domain/price_levels.js';
// 案内文言は理由コードと同居する単一ソースから取る（ここへ書き写すと 2 か所に割れる）。
import { MSG_NO_SYMBOL_SPEC } from './price_pick_resolver.js';

const MSG_MC_FAILED = '計算できませんでした（モンテカルロの実行に失敗）';

export class PositionSizingController {
  /**
   * @param {object} deps
   * @param {object} deps.usecase   PositionSizingPlanUseCase。
   * @param {object} deps.dialog    PositionSizingDialog（Presenter）。
   * @param {?object} [deps.picker] PricePickController（アーム式ピッカー）。
   * @param {?object} [deps.primitive] PriceLevelLinesPrimitive（水準線の表示先）。
   * @param {?object} [deps.toast]  ChartToastView 互換（show(text)）。
   * @param {{tick:number}|null} [deps.symbolSpec] 銘柄仕様（ISSUE-368 スライス S-6）。
   *   **3 状態を区別する**（resolver と同じ規約）: 未指定（undefined）＝銘柄仕様を扱わない構成
   *   （従来の最小構成・単体検定。量子化しない）／`null`＝解決に失敗（チャートからの価格指定を
   *   落とす）／`{tick}`＝解決できた（水準は刻み上にしか存在できない）。
   */
  constructor({
    usecase, dialog, picker = null, primitive = null, toast = null, symbolSpec,
  } = {}) {
    this._usecase = usecase;
    this._dialog = dialog;
    this._picker = picker;
    this._primitive = primitive;
    this._toast = toast;
    this._symbolSpec = symbolSpec;
    this._mcInFlight = null;   // 実行中の MC（再入ガード・Y-2）
  }

  /**
   * 注入された銘柄仕様（右クリック項目の価格解決が遅延参照する・S-6）。
   * **保持しているものを返すだけ**（解決は共有配線の 1 か所が済ませている）。
   */
  symbolSpec() {
    return this._symbolSpec ?? null;
  }

  /**
   * 水準に効かせる刻み（未注入・未解決なら null＝量子化しない）。
   *
   * 真偽判定だけで済む理由（不変条件）: 注入元の引き当て（front 配下で銘柄仕様を解決する唯一の口）
   * は、量子化に使えない刻みを持つ台帳を「解決できた」として返さない。ゆえに `_symbolSpec` が
   * 真なら `tick` は必ず正の有限数であり、ここで検算を第 2 実装として持たない
   * （判定の唯一源は `domain/price_quantize.js` の `usableTick`）。
   */
  _tick() {
    return this._symbolSpec ? this._symbolSpec.tick : null;
  }

  // ---- モーダルの開閉 ----

  open() {
    this._dialog?.open?.();
    this.render();
  }

  // ---- モーダルからの入力（コールバック注入で結ばれる）----

  setParams(patch) {
    this._present(this._usecase.setParams(patch));
  }

  // 水準は価格だけを受け取り、不変条件は domain（E-02）が持つ。
  //   未入力（null）を含む入力でも作り直す: 計算は権威（domain）が「ロット 0」で答え、
  //   モーダルは非有限を「—」で出す。ここで入力途中を判定して弾くと、判定が 2 か所になる。
  //   刻み（tick）はここで**必ず**添える。モーダルは価格しか知らず、水準を作るのは本 class の
  //   責務だからである（domain が丸めるので、front 側に丸めの第 2 実装は生まれない）。
  setLevels(spec) {
    this._present(this._usecase.setLevels(createPriceLevels({ ...spec, tick: this._tick() })));
  }

  /**
   * 水準線 drag が作った PriceLevels をそのまま取り込む（チャート → モーダルの方向）。
   * モーダルの価格欄は `syncPrices`（通知しない書き戻し）で追随させる＝エコーを作らない。
   */
  applyLevels(levels) {
    const vm = this._usecase.setLevels(levels);
    this._dialog?.syncPrices?.(vm.levelLines);
    this._present(vm);
  }

  /**
   * いまの水準（domain 実体）。drag の掴み対象・非破壊更新の元になる。
   * **保持はしない**（所有者は usecase 1 か所）。ここに写しを置くと、両方を書く経路を
   * 通らない更新で「計算に使う水準」と「掴む水準」が割れる（TC-PC14 が固定）。
   */
  levels() {
    return this._usecase.levels();
  }

  // MC は数秒かかる（grid 60 点 × sims × T）。進捗を中継しないと「押しても何も起きない」と
  //   区別できない（NFR-09「MC 実行中もチャート操作が固まらない／進捗が進む」）。
  //   比の解釈も書式も持たず**そのまま渡す**（表示は Presenter の責務）。
  //   完了・失敗のどちらでも必ず消す（残すと「まだ計算中」に見える）。
  // 実行中に押し直しても**新しい MC を始めない**（Y-2）。gateway は solve 1 回につき Worker を
  //   1 つ作るため、連打すると Worker が積み上がり、最後に決着したものが表示を上書きする
  //   （どれが今の入力の結果なのか分からなくなる）。実行中は同じ Promise を返す。
  //   決着（成功・失敗のいずれでも）で必ずガードを外す＝押せないまま張り付かない。
  runMonteCarlo() {
    if (this._mcInFlight) {
      return this._mcInFlight;
    }
    this._mcInFlight = this._runMonteCarlo().finally(() => {
      this._mcInFlight = null;
    });
    return this._mcInFlight;
  }

  async _runMonteCarlo() {
    const onProgress = (ratio) => this._dialog?.setProgress?.(ratio);
    try {
      this._present(await this._usecase.runMonteCarlo(onProgress));
    } catch (err) {
      // 無音の縮退をしない（押しても何も起きない状態を作らない）。原因は console にも残す。
      // eslint-disable-next-line no-console
      console.error('[position-sizing] MC 実行に失敗:', err);
      this._toast?.show?.(MSG_MC_FAILED);
    } finally {
      this._dialog?.setProgress?.(null);
    }
  }

  // ---- チャートからの入力（R-P1 ピッカー / R-P3 右クリック）----

  /**
   * モーダルの「チャートで指定」→ ピッカーをアームする。
   *
   * 銘柄仕様が解決できていないときは**アームしない**（設計「フェイルセーフ」: ピッカーは確定しない）。
   * 無音で機能だけ死ぬのを避けるため、理由をトーストで告知し console にも残す。
   * 手入力は落とさない（人が打った値は人の責任で使う）＝ここで落とすのはチャート由来の経路だけ。
   */
  requestPick(target) {
    if (this._symbolSpec === null) {
      // eslint-disable-next-line no-console
      console.error(`[position-sizing] ${MSG_NO_SYMBOL_SPEC}（target=${target}）`);
      this._toast?.show?.(MSG_NO_SYMBOL_SPEC);
      return;
    }
    this._picker?.arm?.(target);
  }

  /** モーダルが閉じた（× ・取消）→ アームも解除する（R-P1「モーダル側の取消で解除」）。 */
  cancelPick() {
    this._picker?.disarm?.();
  }

  /**
   * アーム状態の変化をモーダルへ中継する（実 UI 実測 2026-08-20 の是正）。
   * アーム中はモーダルを非モーダル化しないとチャートを覆ったままになり、
   * ホバーもクリックもできず R-P1 が成立しない。判断も表示も持たず**中継するだけ**。
   */
  setPicking(armed, target = null) {
    this._dialog?.setPicking?.(armed, target);
  }

  /**
   * ピッカー・右クリックの確定 → モーダルの当該欄へ書き戻す（唯一の書き戻し経路）。
   *
   * 閉じているときは**開いてから**書き戻す。書き戻し先の入力欄は `close()` で捨てられるため、
   * 開かずに書くと `setPrice` が黙って抜けて**完全無音**になる（工程 5 🔴-1・node 再現済み）。
   * 右クリックの意図は「この価格を計算機へ入れる」であり、行き先を用意するのが筋。
   */
  confirmPick(target, price) {
    this._ensureOpen();
    this._dialog?.setPrice?.(target, price);
    this._syncPricesFromModel();
  }

  /**
   * 価格欄の表示を**モデル（水準）に合わせ直す**（通知しない書き戻し）。
   *
   * なぜ必要か: チャート由来の価格は生の浮動小数（実測 `62707.710070965324`）で届く。水準そのものは
   * domain の関門が刻みへ丸めるが、欄に書いた文字列は書いたままなので「欄は生値・水準は刻み上・
   * ゴーストは丸めた表示」と 3 者が食い違う（ISSUE-368 の症状そのもの）。**表示はモデルから導く**
   * ことで、front に丸めの第 2 実装を作らずに一致させる。
   *
   * 手入力（'input' イベント）では**呼ばない**: 打っている途中の文字列（'62707.'）を毎回モデルの
   * 数値で上書きすると、打った文字が消える。呼ぶのは外からの書き戻し（ピッカー・右クリック）だけ。
   */
  _syncPricesFromModel() {
    this._dialog?.syncPrices?.(this._usecase.viewModel().levelLines);
  }

  /**
   * 手入力の確定（change / blur）→ 価格欄の表示をモデル（水準）へ合わせ直す（D-3）。
   *
   * ピッカー・右クリックと**同じ 1 本**（`_syncPricesFromModel`）を使う。手入力だけ別の
   * 合わせ方を持つと、経路ごとに表示規則が割れる（原因 β と同型）。
   * 仕様が未解決（tick=null）のときは domain が丸めないので、書き戻しても打った値のまま
   * ＝手入力を落とさないというフェイルセーフが自動的に保たれる。
   */
  commitPrices() {
    this._syncPricesFromModel();
  }

  // 閉じていれば開く（開いていれば何もしない＝入力中の値を作り直さない）。
  _ensureOpen() {
    const dialog = this._dialog;
    if (dialog && typeof dialog.isOpen === 'function' && !dialog.isOpen()) {
      this.open();
    }
  }

  setStopPrice(price) {
    this.confirmPick('stop', price);
  }

  setTakePrice(price) {
    this.confirmPick('take', price);
  }

  /** 右クリック「この価格を建値に追加」→ 建値を 1 本増やす（閉じていれば開いてから）。 */
  addEntryPrice(price) {
    this._ensureOpen();
    this._dialog?.addEntryPrice?.(price);
    this._syncPricesFromModel();
  }

  // ---- 表示 ----

  render() {
    this._present(this._usecase.viewModel());
  }

  // ViewModel を 2 つの表示先へ配る。どちらにも**同じ VM**を渡す（表示が割れない）。
  _present(vm) {
    this._dialog?.render?.(vm);
    this._primitive?.setLevels?.(vm.levelLines);
  }
}
