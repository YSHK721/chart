// replay_controller_port.js — setupReplay が controller に要求する面の**唯一の宣言**（ISP・ISSUE-502 段階 5B）。
//
// 問題（台帳 .doc/solid_audit_20260906.md「replay_ui/web/js/replay.js — typeof 能力探査 18 箇所＝
//   宣言 port の代用【高】」・実測 2026-09-06）:
//   replay.js は `typeof controller.X === 'function'` を **19 箇所**（10 メソッド）に書いて、
//   controller が「その面を持つか」を**呼ぶ直前に毎回**問い合わせていた。要求する面がどこにも
//   宣言されておらず、探査の集合を読み取るには driver 本体を全行走査するしかない状態である。
//   さらに探査は「面の一部だけを持つ controller」を無言で受理するため、面の欠落が
//   「動くが何も起きない」形で埋もれる（ISSUE-291 と同型の壊れ方）。
//
// 本モジュールの役割:
//   要求する面を「必須 / 面（すべて揃うか 1 つも無いか）/ 独立任意」の 3 区分で宣言し、
//   **接続時に 1 回だけ**突き合わせて、実装があれば host へ bind したもの、無ければ
//   **宣言済みの不在時実装**を入れた凍結オブジェクト（port）を返す。以後 driver は
//   `port.windowTokenOf(...)` と**直接呼ぶ**（呼ぶたびの能力探査を発行しない）。
//
// 設計（SOLID）:
//   - ISP: driver が要る面だけを宣言する。宣言に無いものは port に現れない。
//   - DIP: driver は具象 controller ではなく本契約に依存する。契約はクライアント（driver）が所有し、
//     controller はそれを満たす側になる。
//   - LSP: 実装は host へ bind して返すため、subclass（ReplayIndicatorController）の override が
//     そのまま効く。
//   - OCP: 面を増やすときに触るのは本ファイルの宣言 1 箇所である。
//
// 非目標（本 port の対象外）:
//   **フィールド面**（`_timeframe` / `_recentBars` / `_state` / `_meta` / `_isMarketProfile` /
//   `applyIndicator` / `setTimeframe`）は本 port が扱わない。driver は従来どおり controller へ
//   直接触れる（ISSUE-502 段階 5B の範囲は「実行時能力探査の除去」であり、フィールド面の
//   射影は別件である）。同様に、driver が controller を素のまま渡す協働子
//   （`FormingPlanCache` / `FormingAnimator`）が要求する面も本契約の範囲外で、
//   その 2 者が自分の要求を所有する。

/** 何もしない不在時実装（戻り値を使わない面の既定）。 */
const noop = () => {};

/**
 * 必須の面。1 つでも欠ければ**接続時に** TypeError。
 *
 * driver が無条件に呼ぶメソッドだけを挙げる（実測: `setUntilTime` は replay.js の
 * render / disable、`recomputeAllApplied` は render / catchUpToLiveTail から
 * 分岐なしで呼ばれる）。
 */
export const REQUIRED_METHODS = Object.freeze(['setUntilTime', 'recomputeAllApplied']);

/**
 * 面（face）の宣言: **すべて揃うか、1 つも無いか**の 2 状態しか受理しない集合。
 *
 * `revealStore`（因果リビールの全長系列保管庫・ISSUE-158 / ISSUE-296）は
 * `ReplayIndicatorController` が 9 メソッドを**まとめて**持ち、共有ベース
 * `IndicatorController` は 1 つも持たない（実測 2026-09-06）。つまり実在する状態は
 * 「全部ある」か「全部無い」の 2 つだけである。部分実装は契約違反として
 * **接続時に落とす**——旧実装（メソッドごとの typeof）は部分実装を無言で受理し、
 * 面の欠落を実行時まで隠していた。
 *
 * 各値は「面が不在のときの実装」であり、旧 typeof 分岐の else 側と 1 対 1 で同値になるよう
 * 選んである（同値性の追跡は tests/replay_controller_port.test.js が固定する）。
 */
export const OPTIONAL_FACES = Object.freeze({
  revealStore: Object.freeze({
    // 窓の同一性トークン。不在時 null＝旧 else 側（presentToken=null / token=null）と同値。
    windowTokenOf: () => null,
    // 一括リビール基底の保管庫からの充填。不在時は何もしない（旧: 呼ばない）。
    seedRevealFromStore: noop,
    // 当該窓の全長系列を保管庫が持つ instanceId 集合。不在時 null＝旧 else 側（storedIds=null）と同値。
    storedInstanceIds: () => null,
    // 基底の構築要否。不在時 false＝旧 else 側（構築ブロックを通らない）と同値。
    revealNeedsBuild: () => false,
    // 基底の構築。`revealNeedsBuild()` が false のとき到達しない（面を総にするための宣言）。
    buildRevealBase: async () => {},
    // 基底キャッシュ有無。不在時 false＝旧 else 側（skip 述語の第 1 項が false）と同値。
    hasRevealFor: () => false,
    // 同期リビール描画。不在時は何もしない（旧: 呼ばない）。
    revealTo: noop,
    // 保管庫からの同期描画。不在時 null＝旧 else 側（drawn=null）と同値。
    renderStored: () => null,
    // 基底キャッシュ全破棄。不在時は何もしない（旧: 呼ばない）。
    clearRevealCache: noop,
  }),
});

/**
 * 独立任意の面: 面としてまとまらず、単独で在ったり無かったりするもの。
 *
 * 実測（2026-09-06）: 共有ベース `IndicatorController` は 2 つとも持つが、driver の
 * テスト fake は `setTimeframeApplier` だけを持つものが実在する。よって束ねられない。
 */
export const OPTIONAL_METHODS = Object.freeze({
  // 適用/削除の購読スロット（ISSUE-037）。不在時は何もしない。
  setAppliedObserver: noop,
  // 時間足切替の反映役スロット（ISSUE-231）。不在時は何もしない。
  setTimeframeApplier: noop,
});

/** 契約が言及する全メンバー名（宣言の網羅を検定が突き合わせるための面）。 */
export const CONTRACT_MEMBERS = Object.freeze([
  ...REQUIRED_METHODS,
  ...Object.values(OPTIONAL_FACES).flatMap((face) => Object.keys(face)),
  ...Object.keys(OPTIONAL_METHODS),
]);

/** 接続時に 1 回だけ解決される任意メンバー名（＝旧 typeof 探査の対象だったもの）。 */
export const RESOLVED_OPTIONAL_MEMBERS = Object.freeze([
  ...Object.values(OPTIONAL_FACES).flatMap((face) => Object.keys(face)),
  ...Object.keys(OPTIONAL_METHODS),
]);

/** host が name を「メソッドとして」持つか（**接続時の 1 回だけ**行う面の判定）。 */
function hasMethod(host, name) {
  return typeof host[name] === 'function';
}

/**
 * host の**現在の**実装へ転送する呼び出し口を作る（遅延束縛）。
 *
 * 束縛せず毎回 `host[name]` を引くのは、旧実装 `controller.X(...)` と**同一の解決規則**を
 * 保つためである。接続時に `bind` で焼き付けると、接続後に差し替えられた実装
 * （subclass の override・テストが後から差し替える spy）を観測できなくなる。
 * 実測（2026-09-06）: `tests/replay_timeframe_applier.test.js:82` は setupReplay 後に
 * `controller.recomputeAllApplied` を差し替えて反映役を起動する。焼き付けると本検定が落ちる。
 *
 * ここで**消しているのは面の探査**（メソッドが在るかの毎回の問い合わせ）であって、
 * 呼び出し先の解決ではない。
 */
function forwardTo(host, name) {
  return (...args) => host[name](...args);
}

/**
 * controller を本契約へ突き合わせ、凍結した port を返す（**接続時 1 回だけ**呼ぶこと）。
 *
 * @param {object} controller 突き合わせ対象（`ReplayIndicatorController` 等）。
 * @returns {Readonly<object>} 契約の全メンバー（実装 or 宣言済み不在時実装）と、
 *   面・独立任意の充足を表す凍結 `supports` を持つオブジェクト。
 * @throws {TypeError} 必須メンバーの欠落、または面の**部分実装**。
 */
export function createReplayControllerPort(controller) {
  if (controller == null) {
    throw new TypeError('createReplayControllerPort: controller が null です');
  }
  const port = {};
  const supports = {};

  // --- 必須（欠落は接続時に落とす） ---
  const missingRequired = [];
  for (const name of REQUIRED_METHODS) {
    if (!hasMethod(controller, name)) {
      missingRequired.push(name);
      continue;
    }
    port[name] = forwardTo(controller, name);
  }
  if (missingRequired.length) {
    throw new TypeError(
      'ReplayControllerPort: controller に必須メンバーがありません:'
      + ` ${missingRequired.join(', ')}。`
      + ' setupReplay はこれらを分岐なしで呼ぶ（不在は無言で壊れるため接続時に落とす）。'
    );
  }

  // --- 面（すべて揃うか 1 つも無いか。部分実装は接続時に落とす） ---
  for (const [faceName, members] of Object.entries(OPTIONAL_FACES)) {
    const names = Object.keys(members);
    // 面の判定はここで 1 回だけ（以後どのフレームでも問い合わせ直さない）。
    const present = names.filter((name) => hasMethod(controller, name));
    if (present.length !== 0 && present.length !== names.length) {
      const missing = names.filter((name) => !present.includes(name));
      throw new TypeError(
        `ReplayControllerPort: 面 ${faceName} が部分実装です。欠落: ${missing.join(', ')}。`
        + ' 本面は「すべて揃う」か「1 つも無い」かのどちらかである（部分実装を受理すると'
        + '、面の欠落が実行時まで表に出ない）。'
      );
    }
    const complete = present.length === names.length;
    supports[faceName] = complete;
    for (const name of names) {
      port[name] = complete ? forwardTo(controller, name) : members[name];
    }
  }

  // --- 独立任意（不在は宣言済み実装で埋める） ---
  for (const [name, absent] of Object.entries(OPTIONAL_METHODS)) {
    const present = hasMethod(controller, name);
    supports[name] = present;
    port[name] = present ? forwardTo(controller, name) : absent;
  }

  port.supports = Object.freeze(supports);
  return Object.freeze(port);
}
