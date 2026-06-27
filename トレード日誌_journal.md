# トレード日誌

新しい記録は上に追記(新しい日付が上)。

---

## 2026-06-27
- システム構築。運用プロファイル確定: スイング/IPO重視、日米個別株+日米IPO、リスク中間、執行=Claudeが楽天証券の発注画面まで準備→最終クリックは本人(認証/2FAはClaudeは触らない)。
- 記憶ファイル一式を作成。
- 永続化方針を決定: マスターはGitHub非公開リポ。セキュリティ上、メイン垢(toshi255)ではなく専用bot垢 `kotobuki5stromclaude-hub` をClaudeに連携(最小権限=被害範囲をこの1リポに限定)。
- リポを bot垢直下 `kotobuki5stromclaude-hub/stock-trade-automation-by-claude` に確定。git pushは環境制約で不可のためGitHub API経由で読み書き(`gh_api.py`)。
- 次: スケジュール起動(寄り前/大引け後/米国寄り前)の設定 / 初回スクリーニング。
