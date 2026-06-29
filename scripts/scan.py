#!/usr/bin/env python3
"""
自動スキャンスクリプト（完全テスト運用・Claude自律判断版）
GitHub Actionsから呼び出される。
買い/売り判断・損切り/利確ラインの設定までClaudeが自律的に行い、CSVに記録する。
kotobのコメントはオプションの介入として扱う（必須ではない）。
"""
import os, sys, json, subprocess, base64, datetime, urllib.parse, re

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

def call_claude(prompt, model="claude-haiku-4-5-20251001", max_tokens=3000):
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

# ── kotobの任意コメント処理（介入があれば優先） ─────────────────────

def parse_action_comments(actions_md):
    pending = []
    lines = actions_md.splitlines()
    in_table = False
    for line in lines:
        if line.startswith("| 銘柄") or line.startswith("|---"):
            in_table = True
            continue
        if in_table and line.startswith("|") and "← ここに記入" not in line:
            cols = [c.strip() for c in line.split("|")[1:-1]]
            if len(cols) >= 5:
                name, code, action, reason, comment = cols[0], cols[1], cols[2], cols[3], cols[4]
                if comment and comment not in ("", "← ここに記入", "（対応済み）"):
                    pending.append({"name": name, "code": code, "action": action,
                                     "reason": reason, "comment": comment})
        elif in_table and not line.startswith("|"):
            in_table = False
    return pending

# ── Claude自律判断（買い/売り + 損切り/利確） ────────────────────────

def autonomous_decision(scan_type, watchlist, positions, rules, journal_tail,
                        holdings_csv, summary_csv, decisions_csv, kotob_notes):
    now = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=9)))
    date_str = now.strftime("%Y-%m-%d %H:%M JST")

    scan_labels = {
        "japan_morning":   "日本株スキャン（寄り前 10:00 JST）",
        "japan_afternoon": "日本株スキャン（大引け前 13:00 JST）",
        "us_morning":      "米国株スキャン（米国寄り前 0:00 JST）",
        "weekly":          "週次レビュー（金曜 16:00 JST）",
    }
    label = scan_labels.get(scan_type, scan_type)

    notes_section = f"\n## kotobからの任意コメント（参考にする。介入がなければ無視してClaude判断で進める）\n{kotob_notes}\n" if kotob_notes else ""

    prompt = f"""あなたは株式売買サポートシステムのClaudeです。
実行日時: {date_str}
スキャン種別: {label}

## 重要な前提
- **完全テスト運用（ペーパートレード）**。実際の資金・実弾発注は一切行わない。
- 買い/売りの最終判断もClaudeが自律的に行う（kotobの承認待ちは不要）。
- 損切り・利確ラインもClaudeが地合い・ファンダ・地政学を考慮して自動計算する。
- kotobのコメントがあれば参考にするが、なくても自律的に判断・実行する。
- **ウォッチリストが空でも止まらない**。事前登録された銘柄がなくても、今回の地合い・テーマ（AI・半導体・ロボット・高市政権戦略分野・IPO等）から自分でスクリーニングし、具体的な銘柄コードを挙げて判断する。「スクリーニング未実施」で待つのではなく、今回のスキャンの中で完結させる。
- ⚠️ これは投資助言ではなく、テスト運用内の自己学習用シミュレーションである。
{notes_section}
## 売買ルール
{rules}

## 現在のウォッチリスト（空の場合は自分でスクリーニングして候補銘柄を挙げる）
{watchlist}

## 保有ポジション
{positions}

## 現在の holdings.csv
{holdings_csv}

## 現在の holdings_summary.csv
{summary_csv}

## 直近のトレード日誌（末尾）
{journal_tail}

## 依頼
今回のスキャンで以下を行い、JSON形式で結果を返してください。

1. 地合い・地政学・テーマ性から、今回のスキャンで注目すべき具体的な銘柄を自分でスクリーニングする（ウォッチリストが空でもここで完結させる。実在する銘柄コードを使うこと）
2. 保有銘柄を分析
3. 新規エントリー（買い）すべき銘柄があれば判断し、エントリー価格・損切り・利確ラインを計算（ポジションサイジングは売買ルールの計算手順に従う）
4. 既存保有銘柄で売却（損切り/利確/継続保有）すべきか判断
5. 判断結果をCSVに記録する内容を生成
6. スクリーニングで見つけた候補（買わなかったものも含む）を screened_candidates に記録する

JSON形式（これのみを返す。前後に説明文不要）:

{{
  "market_summary": "地合いサマリー（3〜5行）",
  "decisions": [
    {{
      "type": "buy",
      "code": "銘柄コード",
      "name": "銘柄名",
      "market": "市場",
      "shares": 100,
      "entry_price": 1234.5,
      "currency": "JPY",
      "stop_loss_price": 1148.0,
      "take_profit_price": 1420.0,
      "reasoning": "判断理由（地合い・テクニカル・ファンダ）",
      "holdings_csv_row": "T0005,...(ヘッダーに沿った1行)",
      "decisions_csv_row": "D0005,...(ヘッダーに沿った1行)"
    }}
  ],
  "sells": [
    {{
      "code": "銘柄コード",
      "reason": "損切り/利確/継続保有判断の理由",
      "sell_price": 1234.5,
      "holdings_csv_updated_row": "既存行を売却情報で更新した1行全体"
    }}
  ],
  "screened_candidates": [
    {{ "code": "銘柄コード", "name": "銘柄名", "verdict": "買い/見送り/監視継続", "reason": "理由（1行）" }}
  ],
  "journal_entry": "トレード日誌に残す本文（マークダウン、見出し不要、本文のみ）"
}}

## CSVヘッダー（厳守）
- holdings.csv: 取引ID,銘柄コード,市場,銘柄名,取引種別,取引日時,株数,単価(円/USD),取引総額,通貨,ステータス,売却日時,売却単価,売却総額,損益,損益率,メモ
- trade_decisions.csv: 判断ID,銘柄コード,市場,銘柄名,判断日時,判断種別,判断,根拠カテゴリ,推奨アクション,信頼度,エントリー候補価格,損切り候補価格,利確候補価格,投資方針summary,詳細メモ

新規エントリーも売却もなければ decisions/sells は空配列でよい。JSONのみ返してください。
"""

    print("[scan.py] Requesting autonomous decision from Claude...")
    response_text = call_claude(prompt, max_tokens=4000)

    try:
        json_match = re.search(r'\{.*\}', response_text, re.DOTALL)
        result = json.loads(json_match.group()) if json_match else {}
    except json.JSONDecodeError as e:
        print(f"[scan.py] WARNING: JSON parse error: {e}")
        result = {"market_summary": response_text, "decisions": [], "sells": [],
                  "journal_entry": response_text}

    return result

# ── 判断結果の実行（CSV書き込み） ───────────────────────────────────

def execute_decisions(result, holdings_csv, h_sha, summary_csv, s_sha,
                      decisions_csv, d_sha):
    now = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=9)))
    decisions = result.get("decisions", [])
    sells = result.get("sells", [])

    new_holdings = holdings_csv or ""
    new_decisions = decisions_csv or ""

    for d in decisions:
        row = d.get("holdings_csv_row")
        if row:
            new_holdings = new_holdings.rstrip() + "\n" + row + "\n"
        drow = d.get("decisions_csv_row")
        if drow:
            new_decisions = new_decisions.rstrip() + "\n" + drow + "\n"

    for s in sells:
        old_row = None
        updated_row = s.get("holdings_csv_updated_row")
        if updated_row:
            # 既存行をコードで探して置換
            code = s.get("code", "")
            lines = new_holdings.splitlines()
            new_lines = []
            replaced = False
            for line in lines:
                if not replaced and line.startswith(code + ",") or (not replaced and f",{code}," in line):
                    new_lines.append(updated_row)
                    replaced = True
                else:
                    new_lines.append(line)
            if not replaced:
                new_lines.append(updated_row)
            new_holdings = "\n".join(new_lines)

    if new_holdings != (holdings_csv or ""):
        ok = write_file("data/holdings.csv", new_holdings,
                        f"auto-trade: holdings更新 {now.strftime('%Y-%m-%d %H:%M JST')}", h_sha)
        print(f"[scan.py] holdings.csv update: {'OK' if ok else 'FAILED'}")

    if new_decisions != (decisions_csv or ""):
        ok = write_file("data/trade_decisions.csv", new_decisions,
                        f"auto-trade: decisions更新 {now.strftime('%Y-%m-%d %H:%M JST')}", d_sha)
        print(f"[scan.py] trade_decisions.csv update: {'OK' if ok else 'FAILED'}")

    return len(decisions) > 0 or len(sells) > 0

# ── メイン ─────────────────────────────────────────────────────────

def main():
    print(f"[scan.py] scan_type={SCAN_TYPE}")

    watchlist, _     = read_file("ウォッチリスト_watchlist.md")
    positions, _     = read_file("保有ポジション_positions.md")
    rules, _         = read_file("売買ルール_trading_rules.md")
    journal, j_sha   = read_file("トレード日誌_journal.md")
    actions, a_sha   = read_file("アクション候補_actions.md")
    holdings, h_sha  = read_file("data/holdings.csv")
    summary, s_sha   = read_file("data/holdings_summary.csv")
    decisions, d_sha = read_file("data/trade_decisions.csv")

    if not all([watchlist, positions, rules, journal]):
        print("ERROR: ファイル読み込み失敗"); sys.exit(1)

    # kotobの任意コメントを収集（介入があれば参考材料として渡す）
    kotob_notes = ""
    if actions:
        pending = parse_action_comments(actions)
        if pending:
            kotob_notes = "\n".join([
                f"- {p['name']}({p['code']}): {p['action']} に対して「{p['comment']}」"
                for p in pending
            ])
            print(f"[scan.py] Found {len(pending)} kotob comment(s), will reference them")

    journal_tail = "\n".join(journal.splitlines()[-30:])

    result = autonomous_decision(
        SCAN_TYPE, watchlist, positions, rules, journal_tail,
        holdings or "", summary or "", decisions or "", kotob_notes
    )
    print("[scan.py] Decision received.")

    executed = execute_decisions(result, holdings, h_sha, summary, s_sha, decisions, d_sha)

    now = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=9)))
    scan_labels = {
        "japan_morning": "日本株スキャン", "japan_afternoon": "日本株スキャン",
        "us_morning": "米国株スキャン", "weekly": "週次レビュー",
    }
    label = scan_labels.get(SCAN_TYPE, SCAN_TYPE)

    decisions_text = ""
    for d in result.get("decisions", []):
        decisions_text += f"\n**買い実行**: {d.get('name','')}({d.get('code','')}) {d.get('shares','')}株 @ {d.get('entry_price','')} / 損切り{d.get('stop_loss_price','')} / 利確{d.get('take_profit_price','')}\n- 理由: {d.get('reasoning','')}\n"
    for s in result.get("sells", []):
        decisions_text += f"\n**売却実行**: {s.get('code','')} @ {s.get('sell_price','')}\n- 理由: {s.get('reason','')}\n"
    if not decisions_text:
        decisions_text = "\n（今回は新規エントリー・売却なし）\n"

    screened = result.get("screened_candidates", [])
    screened_text = ""
    if screened:
        screened_text = "\n**今回スクリーニングした銘柄**\n"
        for c in screened:
            screened_text += f"- {c.get('name','')}({c.get('code','')}): {c.get('verdict','')} — {c.get('reason','')}\n"

    entry = f"""
---

## {now.strftime('%Y-%m-%d')} — 自動スキャン ({SCAN_TYPE})

**地合いサマリー**
{result.get('market_summary', '')}

**判断・実行内容**
{decisions_text}
{screened_text}
{result.get('journal_entry', '')}
"""
    new_journal = entry + "\n" + journal

    _, j_sha_fresh = read_file("トレード日誌_journal.md")
    ok = write_file("トレード日誌_journal.md", new_journal,
                    f"scan({SCAN_TYPE}): {now.strftime('%Y-%m-%d %H:%M JST')}", j_sha_fresh)
    print(f"[scan.py] journal updated: {'OK' if ok else 'FAILED'}")
    if not ok:
        sys.exit(1)

if __name__ == "__main__":
    main()
