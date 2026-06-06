# 脅威情報更新の認知手順テンプレート

security スキル §5 実行プロセス S-7（観測・更新計画）から参照される脅威情報更新の認知手順。
更新の起動条件・タイミング・ツール許可は本書の対象外とする。

---

## 情報取得クエリテンプレート（WebSearch）

`<YYYY>` は実行日の西暦年に置換する。

| 検索対象 | クエリ |
|---|---|
| OWASP Top 10 最新版 | `OWASP Top 10 <YYYY> latest version categories ranking` |
| Verizon DBIR 統計 | `Verizon Data Breach Investigations Report <YYYY> statistics` |
| Sonatype SoSC レポート | `Sonatype State of Software Supply Chain <YYYY> statistics` |
| OWASP LLM Top 10 | `OWASP Top 10 for LLM Applications <YYYY> latest version` |
| CISA KEV 重大追加 | `CISA Known Exploited Vulnerabilities <YYYY> critical additions` |
| エッジ機器・ランサムウェア | `edge device VPN vulnerability ransomware <YYYY> trend` |

## 差分検出基準

| 差分種別 | 判定基準 |
|---|---|
| カテゴリ番号変更 | OWASP の `A0X:YYYY` または `LLM0X:YYYY` の年版・順位が変動した場合 |
| 統計値変更 | 引用済み数値が `±5 ポイント` 以上変動した場合（例：30% → 36%） |
| 新規カテゴリ追加 | 既存節に存在しない脅威分類が公式リストに追加された場合 |
| 必須対策の陳腐化 | 推奨アルゴリズム・推奨ツールが非推奨化された場合（例：SHA-1 → 既知衝突） |
| 新興マルウェア確認 | 自己複製型ワーム・サプライチェーン攻撃事例が公的機関に確認された場合 |

## 更新適用規則

| 対象 | 規則 |
|---|---|
| 出典記述 | 最新版のレポート名・公開年・データセット規模を 1 文に統合する |
| 各脅威節の見出し | OWASP カテゴリ番号と年版を `（OWASP A0X:YYYY）` 形式で明記する |
| 「実証根拠」行 | 出典名・統計値・出典の発表年を必ず含める |
| 「必須対策」行 | 推奨アルゴリズム・期限値・閾値を数値で明示し、曖昧表現を排除する |
| 削除対象 | 公式に非推奨化された対策（旧アルゴリズム・廃止 API）は削除する |
| 言い切り形式 | 全節で `である／する` 形式を維持する |
