// Composition Root の共有配線が 1 箇所に閉じていること（ISSUE-278 #4 再発防止）。
//
// 由来: リプレイ合成根はライブ合成根の**全文フォーク**（338 行）だった。同じ配線を 2 か所に手書きで
//   持つため、ライブ側の修正がリプレイへ届かない。実際の取り残し:
//     - `#rp-mode` の option 5 件欠落（commit 4079461）
//     - カテゴリボタンの二重表示（ISSUE-221）
//     - ペイン別凡例の器（ISSUE-277）
//     - `catalog.load` 未呼出＝variant 別受理 param が届かない（ISSUE-278 #8）
//   さらにフォーク側の手書き定数が陳腐化していた（「30m 非対応」の 8 足リスト。実測ではリプレイ core の
//   `/candles?timeframe=30m` も `/compute` も 200 を返す）。
//
// 是正: 共有配線は chart_app_wiring.js が単一ソースとして所有し、各 root は自分固有の差だけを書く。
//   本検定は「共有部品を root が自前で new し直していない」ことをソースで固定する（＝再フォークの禁止）。

import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

const LIVE_ROOT = '../js/adapter/front/composition_root_front.js';
const REPLAY_ROOT = '../../../../simulator/replay_ui/web/js/adapter/front/composition_root_front.js';
const SHARED = '../js/adapter/front/chart_app_wiring.js';

// 共有配線が所有する部品。root が自前で new すると、そこから複製が再発する。
const SHARED_OWNED = [
  'new ChartRenderer(',
  'new CrosshairReadoutView(',
  'new PaneLegendView(',
  'new PaneReorderDrag(',
  'new LocalStorageGateway(',
  'new LocalStorageTemplateGateway(',
  'new IndicatorCatalogClient(',
  'new ChartInteractionController(',
  'new ScrollToLatestButton(',
  'new TimeframeMenu(',
  'new ChartTemplateController(',
  'new ChartTemplateMenu(',
  'new ChartTemplateDialogs(',
  'new TickvolBandsActor(',
  'new TradeMarkersRenderer(',
  'new CurrentPriceView(',
  // ISSUE-368 スライス 7: 計算機一式（メニュー・モーダル・協働子・水準線 primitive・drag・
  //   ピッカー・MC Worker ゲートウェイ）。生成は共有配線が所有し、root は識別子だけを渡す。
  'new PositionSizingMenu(',
  'new PositionSizingDialog(',
  'new PositionSizingController(',
  'new PriceLevelLinesPrimitive(',
  'new PriceLevelDragController(',
  'new PricePickController(',
  'new McWorkerGateway(',
  'new PositionSizingPlanUseCase(',
];

function read(rel) {
  return readFileSync(fileURLToPath(new URL(rel, import.meta.url)), 'utf8');
}

test('両 root が共有配線モジュールを使う（フォークではなく参照）', () => {
  for (const root of [LIVE_ROOT, REPLAY_ROOT]) {
    const src = read(root);
    assert.match(src, /from '\.\/chart_app_wiring\.js'/, `${root} が共有配線を import していない`);
    assert.match(src, /composeChartShell\(/, `${root} が composeChartShell を呼んでいない`);
    assert.match(src, /installSharedUi\(/, `${root} が installSharedUi を呼んでいない`);
    assert.match(src, /wireControllerCollaborators\(/, `${root} が wireControllerCollaborators を呼んでいない`);
  }
});

test('共有部品を root が自前で組み立て直していない（再フォークの検出）', () => {
  for (const root of [LIVE_ROOT, REPLAY_ROOT]) {
    const src = read(root);
    for (const marker of SHARED_OWNED) {
      assert.ok(
        !src.includes(marker),
        `${root} に ${marker} がある（共有配線が所有する部品を root が複製している）`,
      );
    }
  }
});

test('共有配線が実際にそれらを所有している（検定が空振りしない）', () => {
  const shared = read(SHARED);
  for (const marker of SHARED_OWNED) {
    assert.ok(shared.includes(marker), `chart_app_wiring.js に ${marker} が無い`);
  }
});

test('時間足の集合を root が手書きしていない（台帳導出・ISSUE-278 #4）', () => {
  // 旧フォークは validTimeframes と TimeframeMenu groups に 8 足を手書きしていた（30m 欠落）。
  //   台帳（domain/tf_meta.js）からの導出が唯一の定義であることを固定する。
  for (const root of [LIVE_ROOT, REPLAY_ROOT]) {
    const src = read(root);
    assert.ok(!src.includes('validTimeframes'), `${root} が validTimeframes を手書きしている`);
    assert.ok(!/'1m', '5m', '15m'/.test(src), `${root} に時間足コードの手書き列がある`);
  }
  const shared = read(SHARED);
  assert.match(shared, /Object\.keys\(TF_BAR_SEC\)/, '共有配線が台帳から導出していない');
});

test('リプレイ root は自分固有の差だけを持つ（ライブ専用機構を持ち込まない）', () => {
  // リプレイ core には /live_ticks・/forming_bar・/tf_period_profile が無い。ライブ専用の
  //   ポーラ・列アクターを replay root へ複製すると、存在しない経路を叩き始める。
  const src = read(REPLAY_ROOT);
  // 判定は**構築（new）**で行う（散文の言及は対象外＝コメントで説明できる）。
  for (const marker of ['LiveTickPlayer', 'FormingBarUpdater', 'TfPeriodProfileActor', 'LiveFollowController']) {
    assert.ok(!src.includes(`new ${marker}(`), `リプレイ root が ${marker} を構築している`);
  }
  // 逆に、リプレイ固有の差は本 root が持つ（共有側へ漏らさない）。
  assert.match(src, /isVerticalPanBlocked/);
  assert.match(src, /MarketProfileReplayBar/);
  assert.ok(!read(SHARED).includes('MarketProfileReplayBar'), '共有配線にリプレイ固有部品が漏れている');
});
