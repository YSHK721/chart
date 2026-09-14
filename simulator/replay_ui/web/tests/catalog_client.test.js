// catalog_client.js の検定は indicator_ui 側の 1 本に統一する（ISSUE-313）。
//
// 検証対象 ../js/adapter/front/catalog_client.js は indicator_ui の実体を指す symlink であり、
// 従来の 28 行は同じ実装をもう一度検定する写しだった（実際にコメント差が生じていた）。
import '../../../../indigators/indicator_ui/web/tests/catalog_client.test.js';
