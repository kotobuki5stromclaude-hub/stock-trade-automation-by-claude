#!/usr/bin/env python3
"""
自動スキャンスクリプト
GitHub Actionsから呼び出される。Anthropic APIでClaudeにスキャンを依頼し、結果をリポに書き戻す。
kotobのコメントをアクション候補ファイルから読み取り、対応も行う。
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

# ── アクション候補ファイルのコメント処理 ────────────────────────────

def parse_action_comments(actions_md):
    """kotobコメント欄に記入があるアクション候補を抽出する"""
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
                ticker_name, code, action, reason, comment = cols[0], cols[1], cols[2], cols[3], cols[4]
                # コメントが空・「← ここに記入」以外なら処理対象
                if comment and comment not in ("", "← ここに記入", "（対応済み）"):
                    pending.append({
                        "name": ticker_name, "code": code,
                        "action": action, "reason": reason, "comment": comment
                    })
        elif in_table and not line.startswith("|"):
            in_table = False
    return pending

def handle_comments(pending_items, holdings_csv, holdings_sha,
                    summary_csv, summary_sha, decisions_csv, decisions_sha,
                    actions_md, actions_sha):
    """コメント付きアクション候補をClaudeに渡して対応を判断・実行する"""
    if not pending_items:
        return "コメントなし"

    now = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=9)))
    date_str = now.strftime("%Y-%m-%d %H:%M JST")

    items_text = "\n".join([
        f"- 銘柄: {i['name']}({i['code']}) / アクション: {i['action']} / kotobコメント: {i['comment']}"
        for i in pending_items
    ])

    prompt = f"""あなたは株式売買サポートシステムのClaudeです。
実行日時: {date_str}
テスト運用（ペーパートレード）です。実弾の発注は行いません。

kotobが以下のアクション候補にコメントを記入しました。
対応内容をJSON形式で返してください。

## kotobのコメント一覧
{items_text}

## 現在の holdings.csv
{holdings_csv}

## 現在の holdings_summary.csv
{summary_csv}

## 現在の trade_decisions.csv（末尾20行）
{chr(10).join(decisions_csv.splitlines()[-20:]) if decisions_csv else ""}

## 指示
各コメントに対して適切な対応を判断し、以下のJSONを返してください：

{{
  "responses": [
    {{
      "code": "銘柄コード",
      "comment": "kotobのコメント",
      "interpretation": "コメントの解釈（yes=実行/no=見送り/様子見=保留 など）",
      "action_taken": "実際に取った対応の説明",
      "holdings_csv_update": "holdings.csvに追記する行（不要ならnull）",
      "summary_update": {{
        "code": "銘柄コード",
        "fields": {{"フィールド名": "新しい値"}}
      }},
      "decisions_csv_row": "trade_decisions.csvに追記する行（不要ならnull）",
      "actions_status": "アクション候補ファイルの対応状況欄に書く文字列"
    }}
  ],
  "journal_note": "トレード日誌に残すメモ（1〜3行）"
}}

## 注意
- holdings.csvのヘッダー: 取引ID,銘柄コード,市場,銘柄名,取引種別,取引日時,株数,単価(円/USD),取引総額,通貨,ステータス,売却日時,売却単価,売却総額,損益,損益率,メモ
- holdings_summary.csvのヘッダー: 銘柄コード,市場,銘柄名,投資方針,保有株数,平均取得単価,取得総額,現在株価,評価額,評価損益,評価損益率,最終更新日時
- trade_decisions.csvのヘッダー: 判断ID,銘柄コード,市場,銘柄名,判断日時,判断種別,判断,根拠カテゴリ,推奨アクション,信頼度,エントリー候補価格,損切り候補価格,利確候補価格,投資方針summary,詳細メモ
- 「yes」「やる」「ok」「買い」などは実行と解釈
- 「no」「見送り」「やめ」などは見送りと解釈
- 「様子見」「保留」などは保留と解釈
- JSONのみ返してください（説明文不要）
"""

    print("[scan.py] Processing kotob comments with Claude...")
    response_text = call_claude(prompt, max_tokens=3000)

    # JSON抽出
    try:
        json_match = re.search(r'\{.*\}', response_text, re.DOTALL)
        if not json_match:
            print("[scan.py] WARNING: No JSON in response"); return response_text
        result = json.loads(json_match.group())
    except json.JSONDecodeError as e:
        print(f"[scan.py] WARNING: JSON parse error: {e}"); return response_text

    responses = result.get("responses", [])
    journal_note = result.get("journal_note", "")

    # holdings.csv更新
    new_holdings = holdings_csv
    for resp in responses:
        row = resp.get("holdings_csv_update")
        if row:
            new_holdings = new_holdings.rstrip() + "\n" + row + "\n"

    if new_holdings != holdings_csv:
        ok = write_file("data/holdings.csv", new_holdings,
                        f"action: kotobコメント対応 holdings更新 {now.strftime('%Y-%m-%d %H:%M JST')}",
                        holdings_sha)
        print(f"[scan.py] holdings.csv update: {'OK' if ok else 'FAILED'}")

    # holdings_summary.csv更新（損切り・利確ラインなど）
    # summaryはシンプルにメモのみ更新（価格更新は別途）
    new_summary = summary_csv
    for resp in responses:
        su = resp.get("summary_update")
        if su and su.get("fields"):
            # 対象行を探して該当フィールドを更新
            lines = new_summary.splitlines()
            headers = lines[0].split(",") if lines else []
            updated_lines = []
            for line in lines:
                cols = line.split(",")
                if cols and cols[0] == su["code"]:
                    for field, value in su["fields"].items():
                        if field in headers:
                            idx = headers.index(field)
                            if idx < len(cols):
                                cols[idx] = value
                    updated_lines.append(",".join(cols))
                else:
                    updated_lines.append(line)
            new_summary = "\n".join(updated_lines)

    if new_summary != summary_csv:
        _, s_sha = read_file("data/holdings_summary.csv")
        ok = write_file("data/holdings_summary.csv", new_summary,
                        f"action: kotobコメント対応 summary更新 {now.strftime('%Y-%m-%d %H:%M JST')}",
                        s_sha)
        print(f"[scan.py] holdings_summary.csv update: {'OK' if ok else 'FAILED'}")

    # trade_decisions.csv更新
    new_decisions = decisions_csv or ""
    for resp in responses:
        row = resp.get("decisions_csv_row")
        if row:
            new_decisions = new_decisions.rstrip() + "\n" + row + "\n"

    if new_decisions != decisions_csv:
        _, d_sha = read_file("data/trade_decisions.csv")
        ok = write_file("data/trade_decisions.csv", new_decisions,
                        f"action: kotobコメント対応 decisions更新 {now.strftime('%Y-%m-%d %H:%M JST')}",
                        d_sha)
        print(f"[scan.py] trade_decisions.csv update: {'OK' if ok else 'FAILED'}")

    # アクション候補ファイルの対応状況を更新（「← ここに記入」→実際のコメント+対応済みマーク）
    new_actions = actions_md
    for resp in responses:
        status = resp.get("actions_status", "（対応済み）")
        comment = resp.get("comment", "")
        # コメントが入っている行の対応状況を更新
        # テーブル行のkotobコメント欄を「コメント → 対応済み」に変更
        new_actions = new_actions.replace(
            f"| {comment} |",
            f"| {comment} → {status} |"
        )

    # 過去エントリーへ移動
    for resp in responses:
        action_status = resp.get("actions_status", "対応済み")
        interp = resp.get("interpretation", "")
        new_actions = new_actions.replace(
            "| (まだなし) | | | | |",
            f"| {now.strftime('%Y-%m-%d')} | {resp.get('code','')} | {resp.get('action_taken','')} | {resp.get('comment','')} → {interp} | {action_status} |\n| (まだなし) | | | | |"
        )

    ok = write_file("アクション候補_actions.md", new_actions,
                    f"action: kotobコメント対応状況更新 {now.strftime('%Y-%m-%d %H:%M JST')}",
                    actions_sha)
    print(f"[scan.py] actions.md update: {'OK' if ok else 'FAILED'}")

    return journal_note

# ── プロンプト構築 ───────────────────────────────────────────────────

def build_prompt(scan_type, watchlist, positions, rules, journal_tail, actions_summary=""):
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

    actions_section = f"\n## 前回のkotobコメント対応\n{actions_summary}\n" if actions_summary else ""

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
{actions_section}
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

    watchlist, _        = read_file("ウォッチリスト_watchlist.md")
    positions, _        = read_file("保有ポジション_positions.md")
    rules, _            = read_file("売買ルール_trading_rules.md")
    journal, j_sha      = read_file("トレード日誌_journal.md")
    actions, a_sha      = read_file("アクション候補_actions.md")
    holdings, h_sha     = read_file("data/holdings.csv")
    summary, s_sha      = read_file("data/holdings_summary.csv")
    decisions, d_sha    = read_file("data/trade_decisions.csv")

    if not all([watchlist, positions, rules, journal]):
        print("ERROR: ファイル読み込み失敗"); sys.exit(1)

    # kotobコメント処理
    journal_note = ""
    if actions:
        pending = parse_action_comments(actions)
        if pending:
            print(f"[scan.py] Found {len(pending)} pending comment(s) from kotob")
            journal_note = handle_comments(
                pending, holdings or "", h_sha,
                summary or "", s_sha,
                decisions or "", d_sha,
                actions, a_sha
            )
            # 処理後のアクションファイルを再読み込み（SHAが変わるため）
            actions, a_sha = read_file("アクション候補_actions.md")
        else:
            print("[scan.py] No pending comments in actions file")

    # 通常スキャン
    journal_tail = "\n".join(journal.splitlines()[-30:])
    prompt = build_prompt(SCAN_TYPE, watchlist, positions, rules, journal_tail,
                          actions_summary=journal_note)

    print("[scan.py] Calling Claude for scan...")
    result = call_claude(prompt)
    print("[scan.py] Got response.")

    now = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=9)))
    note_section = f"\n**kotobコメント対応メモ**\n{journal_note}\n" if journal_note else ""
    entry = f"\n---\n\n## {now.strftime('%Y-%m-%d')} — 自動スキャン ({SCAN_TYPE})\n{note_section}\n{result}\n"
    new_journal = entry + "\n" + journal

    # journalのSHAを再取得（actions処理でSHAが変わっている可能性）
    _, j_sha_fresh = read_file("トレード日誌_journal.md")
    ok = write_file("トレード日誌_journal.md", new_journal,
                    f"scan({SCAN_TYPE}): {now.strftime('%Y-%m-%d %H:%M JST')}", j_sha_fresh)
    if ok:
        print("[scan.py] journal updated OK")
    else:
        print("[scan.py] ERROR: journal write failed"); sys.exit(1)

    # アクション候補ファイルに今回のスキャン結果の推奨アクションを追記
    if actions and a_sha:
        now_str = now.strftime("%Y-%m-%d")
        scan_label = {
            "japan_morning": "日本株スキャン(10:00)",
            "japan_afternoon": "日本株スキャン(13:00)",
            "us_morning": "米国スキャン(0:00)",
            "weekly": "週次レビュー",
        }.get(SCAN_TYPE, SCAN_TYPE)

        # 推奨アクション候補セクションを抽出してactions.mdに追記
        action_lines = []
        in_action = False
        for line in result.splitlines():
            if "推奨アクション候補" in line:
                in_action = True
                continue
            if in_action and line.startswith("**") and "推奨アクション候補" not in line:
                break
            if in_action and line.strip() and line.strip() != "なし":
                # 箇条書き行をテーブル行に変換
                text = line.lstrip("- ").strip()
                if text:
                    action_lines.append(text)

        if action_lines:
            new_section = f"\n## {now_str} — {scan_label}\n\n"
            new_section += "| 銘柄 | コード | 候補アクション | 根拠・メモ | kotobコメント |\n"
            new_section += "|------|--------|--------------|-----------|-------------|\n"
            for al in action_lines[:5]:  # 最大5件
                # 「銘柄名(コード): アクション — 根拠」形式を想定
                parts = al.split(":", 1)
                if len(parts) == 2:
                    ticker_part = parts[0].strip()
                    rest = parts[1].strip()
                    reason_parts = rest.split("—", 1) if "—" in rest else rest.split("-", 1)
                    action_text = reason_parts[0].strip() if reason_parts else rest
                    reason_text = reason_parts[1].strip() if len(reason_parts) > 1 else ""
                else:
                    ticker_part = ""
                    action_text = al[:50]
                    reason_text = ""
                new_section += f"| {ticker_part} | | {action_text} | {reason_text} | ← ここに記入 |\n"
            new_section += "\n---\n"

            # 最初の --- の前に挿入
            updated_actions = actions.replace(
                "\n---\n\n## ",
                new_section + "\n## ",
                1
            )
            _, a_sha_fresh = read_file("アクション候補_actions.md")
            ok2 = write_file("アクション候補_actions.md", updated_actions,
                             f"scan({SCAN_TYPE}): アクション候補追記 {now.strftime('%Y-%m-%d %H:%M JST')}",
                             a_sha_fresh)
            print(f"[scan.py] actions.md scan results: {'OK' if ok2 else 'FAILED'}")

if __name__ == "__main__":
    main()
