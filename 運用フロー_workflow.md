# 運用フロー(テスト運用 / ペーパートレード)

最終更新: 2026-06-30

## モード
**実弾の発注は行わない。** 市場分析にもとづき「楽天証券で約定した想定」でCSVに記録するテスト運用。
楽天証券へのブラウザ操作・ログインは使わない。記録はすべてこのリポの `data/` 配下のCSVで管理。

## データファイル(data/) と責務分離

| ファイル | 役割 | 更新方針 |
|---|---|---|
| `data/holdings.csv` | **取引台帳（Source of Truth）** | **append-only**。BUY/SELLは別行追記。既存行の変更・削除は行わない |
| `data/holdings_summary.csv` | 保有サマリー（正本ではない） | **holdings.csv から毎回完全再生成**。直接編集不可 |
| `data/trade_decisions.csv` | 判断ログ（買い/売り/見送りの根拠） | スキャンごとに追記のみ |

> **重要**: holdings_summary.csv を holdings.csv 更新の起点にしないこと。
> 常に holdings.csv を唯一の正本として扱い、summary はそこから算出する。

## 処理フロー（自動スキャン 7ステップ）

```
[GitHub Actions cron]
        ↓
[scripts/scan.py 起動]
        ↓
(1) ファイル読み込み
    ウォッチリスト_watchlist.md / 保有ポジション_positions.md / 売買ルール_trading_rules.md
    トレード日誌_journal.md（末尾30行）/ アクション候補_actions.md
    data/holdings.csv / data/holdings_summary.csv / data/trade_decisions.csv
        ↓
(2) kotobコメント抽出
    アクション候補_actions.md の "kotobコメント" 欄を読み判断に反映する
        ↓
(3) Claude Haiku へ送信（Anthropic API）
    地合い + 保有状況 + ウォッチリスト + ルール → JSON形式で自律判断を要求
        ↓
(4) 判断結果受信（JSON）
    decisions（買い）/ sells（売り）/ holds（継続保有）
    screened_candidates（スクリーニング結果）/ action_items / journal_entry
        ↓
(5) CSV 更新（execute_decisions）
    買い → holdings.csv に新規行追記
          holdings_summary.csv に新規行追記
          trade_decisions.csv に買い判断行追記
    売り → holdings.csv の該当行を売却情報で上書き（ステータス=売却済）
          holdings_summary.csv から該当行を削除
          trade_decisions.csv に売り判断行追記
        ↓
(6) 管理ファイル更新
    トレード日誌_journal.md    — 今回スキャンの判断・結果を先頭に追記
    保有ポジション_positions.md — holdings.csv から再生成
    アクション候補_actions.md  — action_items を先頭に追記
    ウォッチリスト_watchlist.md — 監視継続銘柄を追記（コード未登録のもの）
        ↓
(7) GitHub へ書き戻し
    すべてのファイルを GitHub Contents API（curl）で直接更新
    ※ gitコマンド・ローカルcloneは使用しない
```

## CSV 列仕様

### holdings.csv（14列・append-only）
| 列名 | 例 | 説明 |
|---|---|---|
| 取引ID | T0011 | T+4桁連番。BUY/SELL問わず一意採番 |
| 取引日時 | 2026-06-30 10:15 | YYYY-MM-DD HH:MM |
| 銘柄コード | 6324 | 証券コード or ティッカー |
| 市場 | 東証P | 東証P/S/G / NYSE / NASDAQ |
| 銘柄名 | ハーモニック・ドライブ | 銘柄名称 |
| 取引種別 | BUY | **BUY**=購入 / **SELL**=売却 |
| 株数 | 6 | 取引株数（ミニ株可） |
| 単価 | 7200 | 円 or ドル |
| 取引金額 | 43200 | 株数 × 単価 |
| 手数料 | — | テスト運用は空欄 |
| 税金 | — | テスト運用は空欄 |
| 投資方針 | スイング | スイング / 中長期 / IPO初動 |
| 理由 | ロボット関連テーマ | 売買判断の要約 |
| メモ | テスト約定 | 任意メモ |

> BUYとSELLは独立した行。売却時に既存のBUY行を更新しない。
> 保有株数は BUY株数合計 − SELL株数合計 で算出する。

### trade_decisions.csv（12列）
| 列名 | 例 | 説明 |
|---|---|---|
| 判断日時 | 2026-06-30 10:15 | YYYY-MM-DD HH:MM |
| 判断種別 | 買い判断 | 新規候補/買い判断/売り判断/見送り/保有継続 |
| 銘柄コード | 6324 | — |
| 市場 | 東証P | — |
| 銘柄名 | ハーモニック・ドライブ | — |
| 判断 | 買い | 買い / 売り / 見送り / ホールド |
| 根拠カテゴリ | テクニカル | テクニカル/ファンダ/マクロ/地政学/需給/IPO |
| 根拠詳細 | 25日線押し目 | 詳細テキスト |
| 参考株価 | 7200 | — |
| 推奨アクション | 打診買い | 打診買い/買い増し/一部利確/全売却/様子見 |
| 信頼度 | 高 | 高 / 中 / 低 |
| メモ | — | 任意メモ |

### holdings_summary.csv（14列・holdings.csvから再生成）
| 列名 | 例 | 説明 |
|---|---|---|
| 銘柄コード | 6324 | — |
| 市場 | 東証P | — |
| 銘柄名 | ハーモニック・ドライブ | — |
| 投資方針 | スイング | BUY行の投資方針を引き継ぐ |
| 保有株数 | 6 | BUY株数 − SELL株数 |
| 平均取得単価 | 7200 | 残存保有分の加重平均取得単価 |
| 取得総額 | 43200 | 平均取得単価 × 保有株数 |
| 売却済株数 | 100 | SELL株数の合計 |
| 実現損益 | 0 | (売却単価 − 平均取得単価) × 売却株数 の累計 |
| 現在株価 | 7350 | Yahoo Finance から自動取得（取得失敗時は空欄） |
| 評価額 | 44100 | 現在株価 × 保有株数 |
| 評価損益 | 900 | 評価額 − 取得総額 |
| 評価損益率 | 2.08% | 評価損益 ÷ 取得総額 × 100 |
| 最終更新日時 | 2026-06-30 10:15 | YYYY-MM-DD HH:MM |

> 保有株数が 0 の銘柄（全売却済）はサマリーに含めない。
> 実現損益の計算は平均取得単価法（移動平均）を使用。

## スキャン種別
| scan_type | 実行時刻（JST） | 曜日 | 目的 |
|---|---|---|---|
| japan_morning | 10:00 | 月〜金 | 日本株 寄り前スキャン |
| japan_afternoon | 13:00 | 月〜金 | 日本株 大引け前スキャン |
| us_morning | 0:00 | 火〜土 | 米国株 寄り前スキャン |
| weekly | 16:00 | 金のみ | 週次レビュー |

## 値の入力ガイド（揺れ防止）
- 取引種別(holdings): BUY / SELL
- 判断種別(decisions): 新規候補 / 買い判断 / 売り判断 / 見送り / 保有継続
- 判断(decisions): 買い / 売り / 見送り / ホールド
- 根拠カテゴリ(decisions): テクニカル / ファンダ / マクロ / 地政学 / 需給 / IPO
- 推奨アクション(decisions): 打診買い / 買い増し / 一部利確 / 全売却 / 様子見
- 信頼度(decisions): 高 / 中 / 低
- 市場: 東証P / 東証S / 東証G / NYSE / NASDAQ
- 日時: YYYY-MM-DD HH:MM（JST）
- 取引ID(holdings): T0001 形式の連番

## 注記
- CSVはUTF-8（BOMなし）。Excelで開いて文字化けする場合はBOM付き版を別途出力可。
- 売買はあくまでテスト。実際の資金は動かない。
- GitHub Contents API 経由でファイルを直接更新。gitコマンドは使用しない。
