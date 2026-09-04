// facade.js の検定は indicator_ui 側の 1 本に統一する（ISSUE-313）。
//
// 本スイートが検証していた ../js/usecase/facade.js・../js/domain/domain_models.js は、いずれも
// indicator_ui の実体を指す symlink である（同一 inode）。したがって従来の 298 行は
// 「同じ実装をもう一度検定する写し」であり、実際に片方だけが直された差分（アサーション 1 件の
// 欠落・コメントの食い違い）が発生していた。
//
// import すると node:test は当該モジュール内の test() 登録をそのまま引き継ぐため、本スイートの
// 検定件数は従来どおり保たれる（indicator_ui 側の 1 本が両スイートで走る）。
import '../../../../indigators/indicator_ui/web/tests/facade.test.js';
