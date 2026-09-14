// js_layer_direction.test.js（market_profile パッケージ）— フロント（JS）の依存方向ゲートを
//   market_profile へ展開する（ISSUE-502 段階 3e）。
//
// なぜ在るか（実測 2026-09-07）: 依存方向ゲート（G-1 / G-3）は live / replay / sim / dashboard の
//   4 配信 core にしか置かれていなかった。market_profile は配信 core ではない（自前の配信根を
//   持たず、consumer 側の symlink 経由でのみ配られる）が、`web/js` 直下に domain / usecase /
//   adapter の層を持ち、同じ Dependency Rule に従う。にもかかわらず、走査根として一度も見られて
//   いなかった——live / replay の走査は「自分の配信根の下に見えるもの」を見るので、MP の実体が
//   symlink されていない層（例: MP 側にしか無いファイル）は**どの検定にも入らない**。
//
// 走査の実装は tools/js_layer_guard.mjs が、検定本体（何を assert するか）は
//   tools/js_layer_guard_suite.mjs が唯一持つ。本ファイルは「どの根を見るか」だけを渡す
//   （core ごとに assert を手書きで複製しない＝片方だけ緩む形を作らない）。
//
// ownCore に null を渡す理由: market_profile は配信 URL の第 1 セグメント（`/live` `/replay` …）を
//   持たない。null は「自 core 除外を行わない」＝**最も厳しい側**であり、検査を緩めない
//   （どの core 名を名指しても offender になる）。
//
// 現状 offender 0 の**回帰錨**である。錨が空振りしていないことは、共有スイート内の
//   検出器自己検定（合成ソースで実際に捕捉する）が実証する。

import { fileURLToPath } from 'node:url';
import path from 'node:path';

import { registerLayerDirectionSuite } from '../../../../tools/js_layer_guard_suite.mjs';

const WEB = path.dirname(path.dirname(fileURLToPath(import.meta.url)));

registerLayerDirectionSuite({
  label: 'market_profile',
  jsRoot: path.join(WEB, 'js'),
  repoRoot: path.resolve(WEB, '..', '..', '..'),
  // 配信 core ではない（`/market_profile/...` という配信 URL は存在しない）。
  ownCore: null,
  otherCore: 'live',
  // MP の web/js が持つ層（public は未設置＝構造から見つかるものだけを前提にする）。
  requiredLayers: ['domain', 'usecase', 'adapter'],
  minFiles: 25,
});
