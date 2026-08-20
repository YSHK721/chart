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

const MSG_MC_FAILED = '計算できませんでした（モンテカルロの実行に失敗）';

export class PositionSizingController {
  /**
   * @param {object} deps
   * @param {object} deps.usecase   PositionSizingPlanUseCase。
   * @param {object} deps.dialog    PositionSizingDialog（Presenter）。
   * @param {?object} [deps.picker] PricePickController（アーム式ピッカー）。
   * @param {?object} [deps.primitive] PriceLevelLinesPrimitive（水準線の表示先）。
   * @param {?object} [deps.toast]  ChartToastView 互換（show(text)）。
   */
  constructor({
    usecase, dialog, picker = null, primitive = null, toast = null,
  } = {}) {
    this._usecase = usecase;
    this._dialog = dialog;
    this._picker = picker;
    this._primitive = primitive;
    this._toast = toast;
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
  setLevels(spec) {
    this._present(this._usecase.setLevels(createPriceLevels(spec)));
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
  async runMonteCarlo() {
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

  /** モーダルの「チャートで指定」→ ピッカーをアームする。 */
  requestPick(target) {
    this._picker?.arm?.(target);
  }

  /** ピッカーの確定 → モーダルの当該欄へ書き戻す（唯一の書き戻し経路）。 */
  confirmPick(target, price) {
    this._dialog?.setPrice?.(target, price);
  }

  setStopPrice(price) {
    this.confirmPick('stop', price);
  }

  setTakePrice(price) {
    this.confirmPick('take', price);
  }

  /** 右クリック「この価格を建値に追加」→ 建値を 1 本増やす。 */
  addEntryPrice(price) {
    this._dialog?.addEntryPrice?.(price);
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
