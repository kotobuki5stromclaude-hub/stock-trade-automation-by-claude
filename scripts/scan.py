#!/usr/bin/env python3
"""
自動スキャンスクリプト
GitHub Actionsから呼び出される。Anthropic APIでClaudeにスキャンを依頼し、結果をリポに書き戻す。
"""
import os, sys, json, subprocess, base64, datetime, urllib.parse

REPO = "kotobuki5stromclaude-hub/stock-trade-automation-by-claude"
GH_TOKEN = os.environ["GITHUB_TOKEN"]
ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]
API_BASE = "https://api.github.com"
ANTHROPIC_BASE = "https://api.anthropic.com"

SCAN_TYPE = sys.argv[1] if len(sys.argv) > 1 else "japan_morning"

# ── GitHub API helpers ──────────────────────────────────────────────

def gh_get(path):
    r = subprocess.run(
        ["curl", "-sf", "-H", f"Authorization: token {GH_TOKEN}",
         "-H", "Accept: application/vnd.github+json",
         f"{API_BASE}{path}"],
        capture_output=True, text=True)
    return json.loads(r.stdout or "{}")

def read_file(path, ref="main"):
    d = gh_get(f"/repos/{REPO}/contents/{urllib.parse.quote(path)}?ref={ref}")
    if "content" not in d:
        return None, None
    return base64.b64decode(d["content"]).decode("utf-8"), d["sha"]

def write_file(path, content, message, sha=None, branch="main"):
    body = {"message": message,
            "content": base64.b64encode(content.encode()).decode(),
            "branch": branch}
    if sha:
        body["sha"] = sha
    r = subprocess.run(
        ["curl", "-sf", "-X", "PUT",
         "-H", f"Authorization: token {GH_TOKEN}",
         "-H", "Accept: application/vnd.github+json",
         "-H", "Content-Type: application/json",
         "-d", json.dumps(body),
         f"{API_BASE}/repos/{REPO}/contents/{urllib.parse.quote(path)}"],
        capture_output=True, text=True)
    d = json.loads(r.stdout or "{}")
    return "commit" in d

# ── Anthropic API helper ────────────────────────────────────────────

def call_claude(prompt, model="claude-haiku-4-5-20251001", max_tokens=2000):
    body = {"model": model, "max_tokens": max_tokens,
            "messages": [{"role": "user", "content": prompt}]}
    r = subprocess.run(
        ["curl", "-sf", "-X", "POST",
         "-H", f"x-api-key: {ANTHROPIC_API_KEY}",
         "-H", "anthropic-version: 2023-06-01",
         "-H", "Content-Type: application/json",
         "-d", json.dumps(body),
         f"{ANTHROPIC_BASE}/v1/messages"],
        capture_output=True, text=True)
    d = json.loads(r.stdout or "{}")
    if "content" not in d:
        raise RuntimeError(f"API error: {d}")
    return d["content"][0]["text"]

# ── プロンプト構築 ───────────────────────────────────────────────────

def build_prompt(scan_type, watchlist, positions, rules, journal_tail):
    now = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=9)))
    date_str = now.strftime("%Y-%m-%d %H:%M JST")

    scan_labels = {
        "japan_morning":   "日本株スキャン（寄り前 10:00 JST）",
        "japan_afternoon": "日本株スキャン（大引け前 13:00 JST）",
        "us_morning":      "米国株スキャン（米国寄り前 0:00 JST）",
        "weekly":          "週次レビュー（金曜 16:00 JST）",
    }
    label = scan_labels.get(scan_type, scan_type)

    focus = {
        "japan_morning":   "日本株市場の寄り前状況、本日の地合い、ウォッチリスト銘柄の動向",
        "japan_afternoon": "午後の日本株市場動向、大引けに向けた保有銘柄の損切り・利確判断",
        "us_morning":      "米国株市場の寄り前状況、マクロ・地政学、ウォッチリストの米国株・IPO",
        "weekly":          "今週の振り返り、翌週の注目イベント・マクロ環境、ウォッチリスト見直し",
    }.get(scan_type, "")

    return f"""あなたは株式売買サポートシステムの分析担当Claudeです。
実行日時: {date_str}
スキャン種別: {label}

## システム前提
- テスト運用（ペーパートレード）。実弾の発注は行わない。
- 最終判断は必ずkotob本人。あなたは判断材料（根拠・シナリオ・リスク・反対意見）を整理して出す。
- 「買い/売り推奨」ではなく、判断材料を整理する立場。
- ⚠️ あなたは金融アドバイザーではなく、これは投資助言ではありません。

## 今回のフォーカス
{focus}

## 現在のウォッチリスト
{watchlist}

## 保有ポジション
{positions}

## 売買ルール（抜粋）
{rules[:800]}

## 直近のトレード日誌（末尾）
{journal_tail}

## 依頼
以下の形式でスキャン結果をまとめてください。

### {label} — {now.strftime("%Y-%m-%d")}

**地合いサマリー**
（今日の市場環境・マクロ・地政学を3〜5行で）

**ウォッチリスト確認**
（注目銘柄とその状況を箇条書きで）

**保有銘柄チェック**
（損切り・利確・継続保有の判断材料を箇条書きで）

**推奨アクション候補**
（kotobへの提案。Yes/Noで判断できる形で。なければ「なし」）

**リスク・反対意見**
（上記アクションのリスクや慎重意見）
"""

# ── メイン ─────────────────────────────────────────────────────────

def main():
    print(f"[scan.py] scan_type={SCAN_TYPE}")

    watchlist, _   = read_file("ウォッチリスト_watchlist.md")
    positions, _   = read_file("保有ポジション_positions.md")
    rules, _       = read_file("売買ルール_trading_rules.md")
    journal, j_sha = read_file("トレード日誌_journal.md")

    if not all([watchlist, positions, rules, journal]):
        print("ERROR: ファイル読み込み失敗"); sys.exit(1)

    journal_tail = "\n".join(journal.splitlines()[-30:])
    prompt = build_prompt(SCAN_TYPE, watchlist, positions, rules, journal_tail)

    print("[scan.py] Calling Claude...")
    result = call_claude(prompt)
    print("[scan.py] Got response.")

    now = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=9)))
    entry = f"\n---\n\n## {now.strftime('%Y-%m-%d')} — 自動スキャン ({SCAN_TYPE})\n\n{result}\n"
    new_journal = entry + "\n" + journal

    ok = write_file("トレード日誌_journal.md", new_journal,
                    f"scan({SCAN_TYPE}): {now.strftime('%Y-%m-%d %H:%M JST')}", j_sha)
    if ok:
        print("[scan.py] journal updated OK")
    else:
        print("[scan.py] ERROR: journal write failed"); sys.exit(1)

if __name__ == "__main__":
    main()
