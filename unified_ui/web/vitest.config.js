import { defineConfig } from 'vitest/config';

// unified_ui 自己完結ハーネス。純ロジック検証のため node 環境（DOM 不要）。
// 既存 indicator_ui/replay_ui の設定には一切依存しない。
export default defineConfig({
  test: {
    environment: 'node',
    include: ['tests/**/*.test.js'],
  },
});
