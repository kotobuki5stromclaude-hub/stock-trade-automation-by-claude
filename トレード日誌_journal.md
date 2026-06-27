# トレード日誌

新しい記録は上に追記(新しい日付が上)。

---

## 2026-06-27 (2) — 設計変更: テスト運用へ
- 楽天証券のパスキー/ログインが手間なため、実弾売買は一旦中止。テスト運用(ペーパートレード)に移行。
- データ構造を確定: data/trade_decisions.csv(判断ログ) / data/holdings.csv(取引明細) / data/holdings_summary.csv(保有サマリー)。
- 8ステップの処理フローを 運用フロー_workflow.md に記録。約定は「楽天で売買した想定」でClaudeがCSVに記録する。
- 3CSV(ヘッダーのみ)+フロー文書をリポに投入済み。次: 初回スクリーニング or スケジュール起動。

## 2026-06-27
- システム構築。運用プロファイル確定: スイング/IPO重視、日米個別株+日米IPO、リスク中間、執行=Claudeが楽天証券の発注画面まで準備→最終クリックは本人(認証/2FAはClaudeは触らない)。
- 記憶ファイル一式を作成。
- 永続化方針を決定: マスターはGitHub非公開リポ。セキュリティ上、メイン垢(toshi255)ではなく専用bot垢 `kotobuki5stromclaude-hub` をClaudeに連携(最小権限=被害範囲をこの1リポに限定)。
- リポを bot垢直下 `kotobuki5stromclaude-hub/stock-trade-automation-by-claude` に確定。git pushは環境制約で不可のためGitHub API経由で読み書き(`gh_api.py`)。
- 次: スケジュール起動(寄り前/大引け後/米国寄り前)の設定 / 初回スクリーニング。
