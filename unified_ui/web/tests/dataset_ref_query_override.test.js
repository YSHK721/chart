// 統合ページ（実配信 :8000）の datasetRef を URL クエリで上書きする（ISSUE-447・A-3 案 U1）。
//
// 設計入力: `.doc/MT5_REALTIME_TICK_SUPPLY_BASIC_DESIGN.md` §9 A-3（**承認（U1）**）と
//   §7 H9（front 表示の確認は**実 UI**）。依頼者裁定 2026-09-01「A-3 承認の射程内」。
//
// なぜ統合層にも要るか（実測 2026-09-01）: ルータ `unified_ui/router.py` は `/` に対し
//   `unified_ui/web/index.html` を返す。統合ページが読む合成根は本 `unified_root.js` であり、
//   `indigators/indicator_ui/web/index.html`（スタンドアロン live 入口）は **:8000 では読まれない**。
//   よって indicator_ui 側だけを結線しても実 UI には届かない（ISSUE-291 と同型の「無言の死」）。
//   ref の定数は 2 箇所に重複定義されている（indicator_ui 側入口 / 本統合層）。重複の解消自体は
//   本件の範囲外（裁定により記録のみ）。本検定は**両方が同じ上書き規則に従う**ことを固定する。
//
// 単一ソース厳守: 解決規則の実装は `indigators/indicator_ui/web/js/adapter/front/dataset_ref_query.js`
//   ただ 1 つで、統合層はそれを**参照**する（手書き複製の禁止・memory: no-hand-duplication-single-source）。
//   本検定はその実体を直接 import し、統合層に複製が生まれていないことも併せて固定する。
//
// 参照経路が symlink ではなく `/live/` プロキシである根拠（実測 2026-09-01）:
//   `unified_ui/router.py:326-331` は `os.path.realpath` で解決した実体が `web_root` 配下に
//   無ければ 404 を返す。`unified_ui/web/js/` 配下に他ツリーへの symlink を置くと realpath が
//   `/workspaces/app/indigators/...` に解決して **web_root 外＝404** になる（配信されない）。
//   一方 `unified_root.js` は既に `/live/js/adapter/front/composition_root_front.js` を動的
//   import しており、これは live core のプロキシ経由で実配信される。同じ規約に従う。
//
// 構造は AAA。テスト名は「対象_条件_期待結果」。

import { describe, test, expect } from 'vitest';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

// 単一ソースの実体（統合層に複製を作らず、これを参照する）。
import {
  resolveDatasetRef, DATASET_REF_QUERY_PARAM,
} from '../../../indigators/indicator_ui/web/js/adapter/front/dataset_ref_query.js';
// 他 core を名指してよい唯一の形（公開面 URL）の判定は、依存方向ゲートと同じ実装を使う。
import { PUBLIC_URL_RE } from '../../../tools/js_layer_guard.mjs';

const ROOT_JS = readFileSync(
  fileURLToPath(new URL('../js/unified_root.js', import.meta.url)), 'utf8',
);

//: URL → リポジトリ実体の対応（配信の地形。production の JS は知らない）。
const REPO_ROOT = path.resolve(
  path.dirname(path.dirname(fileURLToPath(import.meta.url))), '..', '..',
);
const LIVE_WEB_ROOT = 'indigators/indicator_ui/web';

const DEFAULT_REF = 'jp225_tick';
const MT5_REF = 'jp225_mt5';

describe('unified_root — datasetRef の URL クエリ上書き（A-3 案 U1）', () => {
  // --- 既定挙動不変（承認条件そのもの） ---
  test('default_ref_constant_is_unchanged', () => {
    // Assert: 既定表示は従来どおり jp225_tick（クエリ無しで 1 ピクセルも変わらない）。
    expect(ROOT_JS).toMatch(/const DATASET_REF = 'jp225_tick';/);
  });

  test('no_query_resolves_to_the_unchanged_default', () => {
    // Arrange / Act / Assert
    expect(resolveDatasetRef('', DEFAULT_REF)).toBe(DEFAULT_REF);
  });

  // --- 上書き（統合ページで ?dataset=jp225_mt5 が効く） ---
  test('dataset_query_overrides_the_ref', () => {
    // Arrange
    const search = `?${DATASET_REF_QUERY_PARAM}=${MT5_REF}`;

    // Act / Assert
    expect(resolveDatasetRef(search, DEFAULT_REF)).toBe(MT5_REF);
  });

  // --- 結線（端から端まで・ISSUE-291 規約） ---
  test('root_passes_resolved_ref_into_bootstrap', () => {
    // Assert: bootstrap へ渡す datasetRef が解決関数の戻り値である（定数直渡しではない）。
    expect(ROOT_JS).toMatch(
      /datasetRef:\s*resolveDatasetRef\(\s*location\.search\s*,\s*DATASET_REF\s*\)/,
    );
  });

  test('root_loads_the_resolver_through_the_public_facade_of_the_live_core', async () => {
    // Arrange（assert 差し替えの記録・ISSUE-479 Wave2b J-5）:
    //   旧 assert は宣言リテラル（`const DATASET_REF_QUERY = '…'`）を読み、LIVE_ROOT と
    //   **同一ディレクトリ・basename が dataset_ref_query.js** であることを固定していた。
    //   固定していた性質は 2 つ——(a) /live プロキシ経路であること（symlink・相対パスにすると
    //   router.py:326-331 の realpath 検査で 404 になる）、(b) 経路が実在の実装を指すこと。
    //   ただし (b) は「同じディレクトリ・同じ basename」という**形の一致**でしか見ておらず、
    //   live core が内部でファイルを動かせば形は合ったまま 404 になる。
    //   よって両方とも強い形へ移す——(a) は「公開面 URL であること」（内部階層の名指しを
    //   そもそも禁じる・G-3 と同じ規律）、(b) は「その公開面が単一ソースと**同一の関数**を
    //   実際に公開していること」（形ではなく到達性）。
    //
    //   経路は定数名ではなく**結線**から辿る。resolveDatasetRef を取り出している import の
    //   引数（識別子）を読み、その識別子の値を URL とする（定数名が変わっても効き続ける）。
    const wiring = ROOT_JS.match(
      /\{\s*resolveDatasetRef\s*\}\s*=\s*await import\(\s*(\w+)\s*\)/,
    );
    expect(wiring, 'resolveDatasetRef を動的 import から取り出していない').not.toBeNull();
    const declared = ROOT_JS.match(new RegExp(`const\\s+${wiring[1]}\\s*=\\s*'([^']+)';`));
    expect(declared, `${wiring[1]} の宣言が無い`).not.toBeNull();
    const url = declared[1];

    // Assert (a): live core の**公開面**だけを名指す（内部階層は配置換えで無言の 404 になる）。
    expect(url.startsWith('/live/')).toBe(true);
    expect(PUBLIC_URL_RE.test(url)).toBe(true);

    // Assert (b): その公開面が実在し、単一ソースと同一の関数・同一の定数を公開している。
    const facade = await import(
      path.join(REPO_ROOT, LIVE_WEB_ROOT, url.slice('/live/'.length))
    );
    expect(facade.resolveDatasetRef).toBe(resolveDatasetRef);
    expect(facade.DATASET_REF_QUERY_PARAM).toBe(DATASET_REF_QUERY_PARAM);
  });

  // --- 単一ソース厳守（手書き複製の禁止） ---
  test('root_does_not_reimplement_the_resolver', () => {
    // Assert: 解決規則の本体（URLSearchParams による dataset 取り出し）が統合層に複製
    //   されていない。複製すると片方だけ直して静かにずれる。
    expect(ROOT_JS).not.toMatch(/new URLSearchParams/);
    expect(ROOT_JS).not.toMatch(/['"]dataset['"]\s*\)/);
  });

  // --- 計算量（無駄の不在）: 解決は起動時 1 回だけ ---
  test('resolver_is_invoked_once_at_boot_not_per_request', () => {
    // Assert: 呼び出しが 1 箇所（fetch ごと・再描画ごとの再解決を作らない）。
    //   固定するのは「毎回解き直す無駄が無い」ことであり、呼び出し回数の仕様化ではない。
    const calls = ROOT_JS.match(/resolveDatasetRef\(/g) || [];
    expect(calls.length).toBe(1);
  });
});
