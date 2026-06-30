#!/usr/bin/env python3
"""
自動スキャンスクリプト（完全テスト運用・Claude自律判断版）
GitHub Actionsから呼び出される。
"""
import os, sys, json, subprocess, base64, datetime, urllib.parse, re

REPO = "kotobuki5stromclaude-hub/stock-trade-automation-by-claude"
GH_TOKEN = os.environ["GITHUB_TOKEN"]
ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]
API_BASE = "https://api.github.com"
ANTHROPIC_BASE = "https://api.anthropic.com"

SCAN_TYPE = sys.argv[1] if len(sys.argv) > 1 else "japan_morning"

# ── GitHub API ──────────────────────────────────────────────────────

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

# ── Anthropic API ────────────────────────────────────────────────────

def call_claude(prompt, model="claude-haiku-4-5-20251001", max_tokens=8000):
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

# ── kotobコメント解析 ────────────────────────────────────────────────

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

# ── Claude自律判断 ───────────────────────────────────────────────────

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
    notes_section = f"\n## kotobからのコメント（参考）\n{kotob_notes}\n" if kotob_notes else ""

    prompt = f"""あなたは株式売買サポートシステムのClaudeです。
実行日時: {date_str}
スキャン種別: {label}

## 重要な前提
- **完全テスト運用（ペーパートレード）**。実際の資金・実弾発注は一切行わない。
- 買い/売りの最終判断もClaudeが自律的に行う。
- 損切り・利確ラインもClaudeが自動計算する。
- ウォッチリストが空でも地合い・テーマから自分でスクリーニングして判断まで完結させる。
- ⚠️ 投資助言ではなく、テスト運用内の自己学習用シミュレーション。
{notes_section}
## 売買ルール
{rules}

## ウォッチリスト
{watchlist}

## 保有ポジション
{positions}

## holdings.csv（現在）
{holdings_csv}

## holdings_summary.csv（現在）
{summary_csv}

## トレード日誌（直近）
{journal_tail}

## 依頼
以下をすべて行い、JSONのみ返してください。

1. 地合い・テーマから今回の注目銘柄をスクリーニング
2. 保有銘柄を分析し、損切り/利確/継続保有を判断
3. 新規エントリー銘柄があればポジションサイジングを計算してエントリー
4. 結果をCSV行として生成

## CSVヘッダー（厳守）
- holdings.csv (15列): 取引ID,ステータス,取得日時,銘柄コード,市場,銘柄名,取得株数,取得単価,取得金額,売却日時,売却株数,売却単価,売却金額,損益,メモ
- trade_decisions.csv (12列): 判断日時,判断種別,銘柄コード,市場,銘柄名,判断,根拠カテゴリ,根拠詳細,参考株価,推奨アクション,信頼度,メモ
- holdings_summary.csv (12列): 銘柄コード,市場,銘柄名,投資方針,保有株数,平均取得単価,取得総額,現在株価,評価額,評価損益,評価損益率,最終更新日時

## CSV行の例
- 新規買い(holdings): T0011,保有中,2026-07-01 10:00,6324,東証P,銘柄名,6,7200,43200,,,,,,テスト約定。理由
- 売却更新(holdings): T0011,売却済,取得日時,6324,東証P,銘柄名,6,7200,43200,2026-07-01 10:00,6,7500,45000,1800,利確
- 買い判断(decisions): 2026-07-01 10:00,買い判断,6324,東証P,銘柄名,買い推奨,テーマ性+テクニカル,詳細,7200,打診買い実行,高,損切り6696/利確8304
- 売り判断(decisions): 2026-07-01 10:00,売り判断,6324,東証P,銘柄名,売り,損切り,損切りライン到達,7000,売却実行,高,損切り執行

## 次の取引IDについて
holdings.csvの最大ID番号から採番してください（例: 最大がT0010なら次はT0011）。

JSONのみ返してください（前後に説明文・コードブロック不要）:

{{
  "market_summary": "地合いサマリー（3〜5行）",
  "decisions": [
    {{
      "type": "buy",
      "trade_id": "T0011",
      "code": "銘柄コード",
      "name": "銘柄名",
      "market": "市場",
      "shares": 100,
      "entry_price": 1234.5,
      "currency": "JPY",
      "stop_loss_price": 1148.0,
      "take_profit_price": 1420.0,
      "reasoning": "判断理由",
      "holdings_csv_row": "T0011,保有中,日時,銘柄コード,市場,銘柄名,100,1234.5,123450,,,,,,テスト約定。理由",
      "decisions_csv_row": "日時,買い判断,銘柄コード,市場,銘柄名,買い推奨,根拠カテゴリ,根拠詳細,1234.5,打診買い実行,高,損切りXXX/利確YYY",
      "summary_csv_row": "銘柄コード,市場,銘柄名,スイング中期,100,1234.5,123450,,,,,"
    }}
  ],
  "sells": [
    {{
      "trade_id": "T0011",
      "code": "銘柄コード",
      "name": "銘柄名",
      "reason": "損切りまたは利確の理由",
      "sell_price": 1234.5,
      "holdings_csv_updated_row": "T0011,売却済,取得日時,銘柄コード,市場,銘柄名,株数,取得単価,取得総額,売却日時,株数,売却単価,売却総額,損益,メモ",
      "decisions_csv_row": "日時,売り判断,銘柄コード,市場,銘柄名,売り,根拠カテゴリ,根拠詳細,売却価格,売却実行,信頼度,メモ"
    }}
  ],
  "holds": [
    {{
      "code": "銘柄コード",
      "name": "銘柄名",
      "reason": "継続保有の理由（簡潔に）"
    }}
  ],
  "screened_candidates": [
    {{ "code": "銘柄コード", "name": "銘柄名", "verdict": "買い/見送り/監視継続", "reason": "理由（1行）" }}
  ],
  "action_items": [
    {{ "code": "銘柄コード", "name": "銘柄名", "action": "kotobに確認してほしいアクション", "memo": "根拠・メモ" }}
  ],
  "journal_entry": "トレード日誌の本文（300字以内・マークダウン）"
}}

**注意**:
- `sells` には実際に売却（損切り/利確）する銘柄のみ含める。継続保有は `holds` に入れる。
- `sells` の sell_price は必ず数値で入れる。
- 新規エントリーも売却もなければ decisions/sells は空配列。
"""

    print("[scan.py] Claude に判断を要求中...")
    response_text = call_claude(prompt)

    try:
        json_match = re.search(r'\{.*\}', response_text, re.DOTALL)
        if not json_match:
            raise ValueError("JSONが見つかりません")
        result = json.loads(json_match.group())
    except (json.JSONDecodeError, ValueError) as e:
        print(f"[scan.py] WARNING: JSONパースエラー: {e}")
        result = {
            "market_summary": "(JSONパースエラー。レスポンスが不正またはトークン超過の可能性)",
            "decisions": [], "sells": [], "holds": [],
            "screened_candidates": [], "action_items": [],
            "journal_entry": f"(スキャンエラー: {e})"
        }

    return result

# ── CSV書き込み ──────────────────────────────────────────────────────

def execute_decisions(result, holdings_csv, h_sha, summary_csv, s_sha,
                      decisions_csv, d_sha):
    now = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=9)))
    decisions = result.get("decisions", [])
    sells = result.get("sells", [])

    new_holdings = holdings_csv or ""
    new_decisions = decisions_csv or ""
    new_summary = summary_csv or ""

    for d in decisions:
        row = d.get("holdings_csv_row")
        if row:
            new_holdings = new_holdings.rstrip() + "\n" + row + "\n"
        drow = d.get("decisions_csv_row")
        if drow:
            new_decisions = new_decisions.rstrip() + "\n" + drow + "\n"
        srow = d.get("summary_csv_row")
        if srow:
            code = d.get("code", "")
            lines = new_summary.splitlines()
            if any(i > 0 and line.split(",")[0] == code for i, line in enumerate(lines)):
                new_summary = "\n".join(
                    srow if (i > 0 and line.split(",")[0] == code) else line
                    for i, line in enumerate(lines)
                )
            else:
                new_summary = new_summary.rstrip() + "\n" + srow + "\n"

    for s in sells:
        updated_row = s.get("holdings_csv_updated_row")
        if not updated_row:
            continue
        trade_id = s.get("trade_id", "")
        code = s.get("code", "")
        lines = new_holdings.splitlines()
        new_lines = []
        replaced = False
        for line in lines:
            if not replaced and (
                (trade_id and line.startswith(trade_id + ",")) or
                (not trade_id and f",{code}," in line)
            ):
                new_lines.append(updated_row)
                replaced = True
            else:
                new_lines.append(line)
        if not replaced:
            new_lines.append(updated_row)
        new_holdings = "\n".join(new_lines)
        drow = s.get("decisions_csv_row")
        if drow:
            new_decisions = new_decisions.rstrip() + "\n" + drow + "\n"
        if code:
            lines = new_summary.splitlines()
            new_summary = "\n".join(
                line for i, line in enumerate(lines)
                if i == 0 or line.split(",")[0] != code
            )

    if new_holdings != (holdings_csv or ""):
        ok = write_file("data/holdings.csv", new_holdings,
                        f"auto-trade: holdings更新 {now.strftime('%Y-%m-%d %H:%M JST')}", h_sha)
        print(f"[scan.py] holdings.csv: {'OK' if ok else 'FAILED'}")

    if new_decisions != (decisions_csv or ""):
        ok = write_file("data/trade_decisions.csv", new_decisions,
                        f"auto-trade: decisions更新 {now.strftime('%Y-%m-%d %H:%M JST')}", d_sha)
        print(f"[scan.py] trade_decisions.csv: {'OK' if ok else 'FAILED'}")

    if new_summary != (summary_csv or ""):
        ok = write_file("data/holdings_summary.csv", new_summary,
                        f"auto-trade: summary更新 {now.strftime('%Y-%m-%d %H:%M JST')}", s_sha)
        print(f"[scan.py] holdings_summary.csv: {'OK' if ok else 'FAILED'}")

    return len(decisions) > 0 or len(sells) > 0

# ── 保有ポジション.md 自動生成 ──────────────────────────────────────

def generate_positions_md(holdings_csv, decisions_csv, now):
    lines = (holdings_csv or "").splitlines()
    active = [l.split(",") for l in lines[1:] if l.strip() and len(l.split(",")) >= 9 and l.split(",")[1] == "保有中"]

    stop_map, profit_map = {}, {}
    for dl in (decisions_csv or "").splitlines()[1:]:
        dc = dl.split(",")
        if len(dc) >= 12 and dc[1] == "買い判断":
            code = dc[2]
            m = dc[11]
            sl = re.search(r'損切り([0-9.]+)', m)
            tp = re.search(r'利確([0-9.]+)', m)
            if sl:
                stop_map[code] = sl.group(1)
            if tp:
                profit_map[code] = tp.group(1)

    total_invested = 0
    rows = []
    for cols in active:
        tid, status, date, code, market, name = cols[0], cols[1], cols[2], cols[3], cols[4], cols[5]
        shares, price, amount = cols[6], cols[7], cols[8]
        try:
            total_invested += float(amount)
        except ValueError:
            pass
        rows.append(
            f"| {name} | {code} | {market} | {shares} | {price:>6} |  "
            f"| {stop_map.get(code, '')} | {profit_map.get(code, '')} |  | {date[:10]} | 保有中 |"
        )

    position_rows = "\n".join(rows) if rows else "| (なし) |  |  |  |  |  |  |  |  |  |  |"
    cash = max(0, 500000 - total_invested)
    cash_ratio = int(cash / 500000 * 100)

    return f"""# 保有ポジション

最終更新: {now.strftime('%Y-%m-%d %H:%M')} JST（自動更新）

## 現在のポジション
| 銘柄 | コード | 市場 | 数量 | 平均取得 | 現値 | 損切り | 利確目標 | 含損益 | エントリー日 | 状態 |
|---|---|---|---|---|---|---|---|---|---|---|
{position_rows}

## 口座サマリー（テスト運用・想定元本50万円）
- 投資中: 約{int(total_invested):,}円 / 現金: 約{int(cash):,}円（現金比率 {cash_ratio}%）
- 同時保有数: {len(active)} / 上限 6銘柄

> ※損切り・利確ラインは trade_decisions.csv 参照。現値・含損益はリアルタイム非対応のため空欄。
"""

# ── アクション候補.md 更新 ───────────────────────────────────────────

def update_actions_md(actions_md, result, scan_type, now):
    action_items = result.get("action_items", [])
    if not action_items:
        return None

    scan_labels = {
        "japan_morning": "日本株スキャン（10:00 JST）",
        "japan_afternoon": "日本株スキャン（13:00 JST）",
        "us_morning": "米国株スキャン（0:00 JST）",
        "weekly": "週次レビュー（16:00 JST）",
    }
    label = scan_labels.get(scan_type, scan_type)
    rows = "\n".join(
        f"| {i.get('name','')} | {i.get('code','')} | {i.get('action','')} | {i.get('memo','')} | ← ここに記入 |"
        for i in action_items
    )
    new_section = f"\n## {now.strftime('%Y-%m-%d')} — {label}\n\n| 銘柄 | コード | 候補アクション | 根拠・メモ | kotobコメント |\n|------|--------|--------------|-----------|-------------|\n{rows}\n"

    md = actions_md or ""
    if "---" in md:
        idx = md.index("---")
        return md[:idx + 3] + new_section + md[idx + 3:]
    return md + new_section

# ── メイン ───────────────────────────────────────────────────────────

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
        print("ERROR: 必須ファイルの読み込み失敗")
        sys.exit(1)

    kotob_notes = ""
    if actions:
        pending = parse_action_comments(actions)
        if pending:
            kotob_notes = "\n".join(
                f"- {p['name']}({p['code']}): {p['action']} に対して「{p['comment']}」"
                for p in pending
            )
            print(f"[scan.py] kotobコメント {len(pending)}件 を参照します")

    journal_tail = "\n".join(journal.splitlines()[-30:])

    result = autonomous_decision(
        SCAN_TYPE, watchlist, positions, rules, journal_tail,
        holdings or "", summary or "", decisions or "", kotob_notes
    )
    print("[scan.py] 判断取得完了")

    execute_decisions(result, holdings, h_sha, summary, s_sha, decisions, d_sha)

    now = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=9)))

    # 日誌エントリ生成
    buy_text = ""
    for d in result.get("decisions", []):
        buy_text += (
            f"\n**買い実行**: {d.get('name','')}({d.get('code','')}) "
            f"{d.get('shares','')}株 @ {d.get('entry_price','')} "
            f"/ 損切り{d.get('stop_loss_price','')} / 利確{d.get('take_profit_price','')}\n"
            f"- 理由: {d.get('reasoning','')}\n"
        )

    sell_text = ""
    for s in result.get("sells", []):
        if s.get("sell_price"):
            sell_text += (
                f"\n**売却実行**: {s.get('name', s.get('code',''))}({s.get('code','')}) "
                f"@ {s.get('sell_price','')}\n"
                f"- 理由: {s.get('reason','')}\n"
            )

    hold_text = ""
    for h in result.get("holds", []):
        hold_text += f"- 継続保有: {h.get('name', h.get('code',''))}({h.get('code','')}) — {h.get('reason','')}\n"

    action_text = buy_text + sell_text
    if not action_text:
        action_text = "\n（今回は新規エントリー・売却なし）\n"

    screened = result.get("screened_candidates", [])
    screened_text = ""
    if screened:
        screened_text = "\n**スクリーニング結果**\n" + "\n".join(
            f"- {c.get('name','')}({c.get('code','')}): {c.get('verdict','')} — {c.get('reason','')}"
            for c in screened
        ) + "\n"

    entry = (
        f"\n---\n\n"
        f"## {now.strftime('%Y-%m-%d')} — 自動スキャン ({SCAN_TYPE})\n\n"
        f"**地合いサマリー**\n{result.get('market_summary', '')}\n\n"
        f"**判断・実行内容**\n{action_text}\n"
        f"{hold_text}"
        f"{screened_text}\n"
        f"{result.get('journal_entry', '')}\n"
    )
    new_journal = entry + "\n" + journal

    _, j_sha_fresh = read_file("トレード日誌_journal.md")
    ok = write_file("トレード日誌_journal.md", new_journal,
                    f"scan({SCAN_TYPE}): 日誌更新 {now.strftime('%Y-%m-%d %H:%M JST')}", j_sha_fresh)
    print(f"[scan.py] 日誌更新: {'OK' if ok else 'FAILED'}")
    if not ok:
        sys.exit(1)

    # 保有ポジション.md 更新
    holdings_fresh, _ = read_file("data/holdings.csv")
    decisions_fresh, _ = read_file("data/trade_decisions.csv")
    _, p_sha = read_file("保有ポジション_positions.md")
    pos_content = generate_positions_md(holdings_fresh or holdings or "", decisions_fresh or decisions or "", now)
    ok = write_file("保有ポジション_positions.md", pos_content,
                    f"scan({SCAN_TYPE}): ポジション更新 {now.strftime('%Y-%m-%d %H:%M JST')}", p_sha)
    print(f"[scan.py] ポジション更新: {'OK' if ok else 'FAILED'}")

    # アクション候補.md 更新
    if result.get("action_items"):
        actions_fresh, a_sha_fresh = read_file("アクション候補_actions.md")
        new_actions = update_actions_md(actions_fresh, result, SCAN_TYPE, now)
        if new_actions:
            ok = write_file("アクション候補_actions.md", new_actions,
                            f"scan({SCAN_TYPE}): アクション候補更新 {now.strftime('%Y-%m-%d %H:%M JST')}", a_sha_fresh)
            print(f"[scan.py] アクション候補更新: {'OK' if ok else 'FAILED'}")

if __name__ == "__main__":
    main()
