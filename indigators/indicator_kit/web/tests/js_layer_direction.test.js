// js_layer_direction.test.js（indicator_kit パッケージ）— フロント（JS）の依存方向ゲートを
//   live/replay 共有部品へ展開する（ISSUE-502 段階 3d）。
//
// なぜ在るか: indicator_kit は配信 core ではない（自前の配信根を持たず、consumer 側
//   —— live の indicator_ui/web/js と replay の simulator/replay_ui/web/js —— の symlink
//   経由でのみ配られる）が、`web/js` 直下に domain / usecase / adapter の層を持ち、同じ
//   Dependency Rule に従う。走査根として登録しないと、live / replay の走査は「自分の配信根の
//   下に見えるもの」しか見ないため、共有部品側にしか無い層の逆流はどの検定にも入らない
//   （chart_kernel を登録した段階 3b・market_profile の段階 3e と同型の穴）。
//
// 走査の実装は tools/js_layer_guard.mjs が、検定本体（何を assert するか）は
//   tools/js_layer_guard_suite.mjs が唯一持つ。本ファイルは「どの根を見るか」だけを渡す。
//
// ownCore に null を渡す理由: indicator_kit は配信 URL の第 1 セグメント（`/live` `/replay` …）を
//   持たない。null は「自 core 除外を行わない」＝**最も厳しい側**であり、検査を緩めない。

import { fileURLToPath } from 'node:url';
import path from 'node:path';

import { registerLayerDirectionSuite } from '../../../../tools/js_layer_guard_suite.mjs';

const WEB = path.dirname(path.dirname(fileURLToPath(import.meta.url)));

registerLayerDirectionSuite({
  label: 'indicator_kit',
  jsRoot: path.join(WEB, 'js'),
  repoRoot: path.resolve(WEB, '..', '..', '..'),
  // 配信 core ではない（`/indicator_kit/...` という配信 URL は存在しない）。
  ownCore: null,
  otherCore: 'live',
  // indicator_kit の web/js が持つ層（public は live core 側に残る＝ここには無い）。
  requiredLayers: ['domain', 'usecase', 'adapter'],
  // 移設した実体は 105 本。下限は「走査が壊れて激減したこと」を検出するためだけの錨。
  minFiles: 80,
});
