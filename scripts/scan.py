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

# ── holdings.csv ヘルパー ────────────────────────────────────────────

def get_next_trade_id(holdings_csv):
    """holdings.csv の最大 T 番号 + 1 を 4 桁ゼロ埋めで返す"""
    max_num = 0
    if holdings_csv:
        for line in holdings_csv.strip().split("\n")[1:]:
            if not line.strip():
                continue
            tid = line.split(",")[0].strip()
            if re.match(r'^T\d+$', tid):
                max_num = max(max_num, int(tid[1:]))
    return f"T{max_num + 1:04d}"


def calculate_positions(holdings_csv):
    """holdings.csv の BUY/SELL を時系列処理し銘柄ごとのポジション状態を返す。

    Returns dict[code] = {
        market, name, policy, entry_date,
        position, avg_cost, sell_qty, realized_pnl
    }
    """
    if not holdings_csv:
        return {}

    rows = []
    for line in holdings_csv.strip().split("\n")[1:]:
        if not line.strip():
            continue
        cols = [c.strip() for c in line.split(",")]
        if len(cols) < 6:
            continue
        try:
            rows.append({
                "datetime": cols[1] if len(cols) > 1 else "",
                "code":     cols[2] if len(cols) > 2 else "",
                "market":   cols[3] if len(cols) > 3 else "",
                "name":     cols[4] if len(cols) > 4 else "",
                "type":     cols[5].upper() if len(cols) > 5 else "",
                "qty":      float(cols[6]) if len(cols) > 6 and cols[6] else 0,
                "price":    float(cols[7]) if len(cols) > 7 and cols[7] else 0,
                "policy":   cols[11] if len(cols) > 11 else "",
            })
        except (ValueError, IndexError):
            continue

    rows.sort(key=lambda r: r["datetime"])

    state = {}
    for r in rows:
        code = r["code"]
        if not code:
            continue
        if code not in state:
            state[code] = {
                "market": r["market"], "name": r["name"], "policy": r["policy"],
                "entry_date": "", "position": 0.0,
                "avg_cost": 0.0, "sell_qty": 0.0, "realized_pnl": 0.0,
            }
        s = state[code]

        if r["type"] == "BUY":
            new_total = s["avg_cost"] * s["position"] + r["price"] * r["qty"]
            if s["position"] == 0:
                s["entry_date"] = r["datetime"][:10]
            s["position"] += r["qty"]
            s["avg_cost"] = new_total / s["position"] if s["position"] > 0 else 0.0
            if r["policy"]:
                s["policy"] = r["policy"]
        elif r["type"] == "SELL":
            s["realized_pnl"] += (r["price"] - s["avg_cost"]) * r["qty"]
            s["sell_qty"] += r["qty"]
            s["position"] -= r["qty"]
            if s["position"] <= 0:
                s["position"] = 0.0
                s["avg_cost"] = 0.0

    return state


def regenerate_summary_csv(holdings_csv, now):
    """holdings.csv から holdings_summary.csv を毎回完全再生成する"""
    state = calculate_positions(holdings_csv or "")
    header = (
        "銘柄コード,市場,銘柄名,投資方針,保有株数,平均取得単価,取得総額,"
        "売却済株数,実現損益,現在株価,評価額,評価損益,評価損益率,最終更新日時"
    )
    date_str = now.strftime("%Y-%m-%d %H:%M")
    rows = []
    for code, s in state.items():
        if s["position"] <= 0:
            continue
        qty = int(s["position"]) if s["position"] == int(s["position"]) else s["position"]
        avg = round(s["avg_cost"], 2)
        total = round(avg * s["position"], 2)
        sell_qty = int(s["sell_qty"]) if s["sell_qty"] == int(s["sell_qty"]) else round(s["sell_qty"], 4)
        realized = round(s["realized_pnl"], 2)
        rows.append(
            f"{code},{s['market']},{s['name']},{s['policy']},"
            f"{qty},{avg},{total},{sell_qty},{realized},,,,"
            f"{date_str}"
        )
    return header + "\n" + "\n".join(rows) + "\n"

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

## CSVヘッダー（参照用）
- holdings.csv (14列・append-only BUY/SELL台帳):
  取引ID,取引日時,銘柄コード,市場,銘柄名,取引種別,株数,単価,取引金額,手数料,税金,投資方針,理由,メモ
  ※ 取引種別 BUY=購入 / SELL=売却。BUY行の更新・削除は行わず、売却はSELL行を追加する。
  ※ holdings.csv の行は scan.py が自動生成するため、JSON に含める必要はありません。
- trade_decisions.csv (12列):
  判断日時,判断種別,銘柄コード,市場,銘柄名,判断,根拠カテゴリ,根拠詳細,参考株価,推奨アクション,信頼度,メモ

## trade_decisions.csv の行の例
- 買い判断: 2026-07-01 10:00,買い判断,6324,東証P,銘柄名,買い,テクニカル,25日線押し目,7200,打診買い実行,高,損切り6696/利確8304
- 売り判断: 2026-07-01 10:00,売り判断,6324,東証P,銘柄名,売り,損切り,損切りライン到達,7000,売却実行,高,損切り執行

JSONのみ返してください（前後に説明文・コードブロック不要）:

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
      "stop_loss_price": 1148.0,
      "take_profit_price": 1420.0,
      "policy": "スイング",
      "reasoning": "判断理由（詳細）",
      "decisions_csv_row": "日時,買い判断,銘柄コード,市場,銘柄名,買い,テクニカル,根拠詳細,1234.5,打診買い実行,高,損切りXXX/利確YYY"
    }}
  ],
  "sells": [
    {{
      "code": "銘柄コード",
      "name": "銘柄名",
      "reason": "損切りまたは利確の理由",
      "sell_price": 1234.5,
      "sell_shares": 0,
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
- `sell_shares` は部分売却の場合のみ指定。省略または 0 の場合は全保有株数を売却する。
- 新規エントリーも売却もなければ decisions/sells は空配列。
- holdings.csv の行は scan.py が自動生成するため、decisions_csv_row のみ返せばよい。
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

def execute_decisions(result, holdings_csv, h_sha, decisions_csv, d_sha):
    """BUY/SELL を holdings.csv に append、decisions を trade_decisions.csv に append。
    holdings_summary.csv はここでは触らず、呼び出し元で regenerate_summary_csv() を使う。
    """
    now = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=9)))
    date_str = now.strftime("%Y-%m-%d %H:%M")

    new_holdings = holdings_csv or ""
    new_decisions = decisions_csv or ""

    cur_positions = calculate_positions(new_holdings)

    # ── BUY ──
    for d in result.get("decisions", []):
        trade_id = get_next_trade_id(new_holdings)
        code     = str(d.get("code", "")).strip()
        name     = d.get("name", "")
        market   = d.get("market", "")
        shares   = d.get("shares", 0)
        price    = d.get("entry_price", 0)
        amount   = round(float(shares) * float(price), 2)
        policy   = str(d.get("policy", "スイング"))[:30]
        reasoning = str(d.get("reasoning", ""))[:60]

        new_holdings = new_holdings.rstrip() + "\n" + (
            f"{trade_id},{date_str},{code},{market},{name},"
            f"BUY,{shares},{price},{amount},,,{policy},{reasoning},テスト約定"
        ) + "\n"

        drow = d.get("decisions_csv_row", "")
        if drow:
            new_decisions = new_decisions.rstrip() + "\n" + drow + "\n"

        print(f"[scan.py] BUY: {trade_id} {code} {name} {shares}株 @ {price}")

    # ── SELL ──
    for s in result.get("sells", []):
        code = str(s.get("code", "")).strip()
        sell_price = s.get("sell_price")
        if not sell_price:
            print(f"[scan.py] SKIP SELL: {code} sell_price 未設定")
            continue

        pos = cur_positions.get(code, {})
        if not pos or pos.get("position", 0) <= 0:
            print(f"[scan.py] SKIP SELL: {code} 保有なし")
            continue

        name   = s.get("name", pos.get("name", ""))
        market = pos.get("market", "")
        policy = pos.get("policy", "スイング")
        specified = s.get("sell_shares", 0)
        sell_shares = int(specified) if specified and int(specified) > 0 else int(pos["position"])
        amount = round(sell_shares * float(sell_price), 2)
        reason = str(s.get("reason", ""))[:60]

        trade_id = get_next_trade_id(new_holdings)
        new_holdings = new_holdings.rstrip() + "\n" + (
            f"{trade_id},{date_str},{code},{market},{name},"
            f"SELL,{sell_shares},{sell_price},{amount},,,{policy},{reason},テスト約定"
        ) + "\n"

        drow = s.get("decisions_csv_row", "")
        if drow:
            new_decisions = new_decisions.rstrip() + "\n" + drow + "\n"

        print(f"[scan.py] SELL: {trade_id} {code} {name} {sell_shares}株 @ {sell_price}")

    if new_holdings != (holdings_csv or ""):
        ok = write_file("data/holdings.csv", new_holdings,
                        f"auto-trade: holdings更新 {date_str} JST", h_sha)
        print(f"[scan.py] holdings.csv: {'OK' if ok else 'FAILED'}")

    if new_decisions != (decisions_csv or ""):
        ok = write_file("data/trade_decisions.csv", new_decisions,
                        f"auto-trade: decisions更新 {date_str} JST", d_sha)
        print(f"[scan.py] trade_decisions.csv: {'OK' if ok else 'FAILED'}")

    return len(result.get("decisions", [])) > 0 or len(result.get("sells", [])) > 0

# ── 保有ポジション.md 自動生成 ──────────────────────────────────────

def generate_positions_md(holdings_csv, decisions_csv, now):
    state = calculate_positions(holdings_csv or "")
    active = {code: s for code, s in state.items() if s.get("position", 0) > 0}

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
    for code, s in active.items():
        qty = int(s["position"]) if s["position"] == int(s["position"]) else s["position"]
        avg = round(s["avg_cost"], 2)
        invested = round(avg * s["position"], 2)
        total_invested += invested
        rows.append(
            f"| {s['name']} | {code} | {s['market']} | {qty} | {avg:,.0f} |  "
            f"| {stop_map.get(code, '')} | {profit_map.get(code, '')} |  | {s.get('entry_date', '')} | 保有中 |"
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

# ── ウォッチリスト.md 更新 ─────────────────────────────────────────────

def update_watchlist_md(watchlist_md, result, now):
    """screened_candidates の「監視継続」銘柄をウォッチリストに追加する（未登録のみ）"""
    monitored = [c for c in result.get("screened_candidates", [])
                 if c.get("verdict") == "監視継続"]
    if not monitored:
        return None

    md = watchlist_md or ""
    date_str = now.strftime("%Y-%m-%d")
    changed = False

    for c in monitored:
        code = str(c.get("code", "")).strip()
        if not code or code in md:
            continue
        name = c.get("name", "")
        reason = c.get("reason", "")[:60]
        is_japan = bool(re.match(r'^\d{3,4}[A-Z]?$', code))
        section_header = "## 日本株(個別)" if is_japan else "## 米国株(個別)"
        new_row = f"| {name} | {code} | ★★ | {reason} |  |  |  | 監視中 | {date_str} |"

        lines = md.splitlines()
        new_lines = []
        in_section = False
        inserted = False

        for line in lines:
            stripped = line.strip()
            if stripped == section_header:
                in_section = True
            elif stripped.startswith("## ") and in_section:
                if not inserted:
                    new_lines.append(new_row)
                    inserted = True
                in_section = False

            if in_section and not inserted and stripped.startswith("| (未)"):
                new_lines.append(new_row)
                inserted = True

            new_lines.append(line)

        if not inserted and in_section:
            new_lines.append(new_row)
            inserted = True

        if inserted:
            md = "\n".join(new_lines)
            changed = True

    if not changed:
        return None

    md = re.sub(r'(最終更新: )[^\n]+', rf'\g<1>{date_str}（自動更新）', md)
    return md

# ── メイン ───────────────────────────────────────────────────────────

def main():
    print(f"[scan.py] scan_type={SCAN_TYPE}")

    watchlist, w_sha = read_file("ウォッチリスト_watchlist.md")
    positions, _     = read_file("保有ポジション_positions.md")
    rules, _         = read_file("売買ルール_trading_rules.md")
    journal, j_sha   = read_file("トレード日誌_journal.md")
    actions, a_sha   = read_file("アクション候補_actions.md")
    holdings, h_sha  = read_file("data/holdings.csv")
    decisions, d_sha = read_file("data/trade_decisions.csv")

    if not all([watchlist, positions, rules, journal]):
        print("ERROR: 必須ファイルの読み込み失敗")
        sys.exit(1)

    # holdings.csv が正本。summary はここで再生成してClaudeへのコンテキストとして渡す
    now_pre = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=9)))
    summary = regenerate_summary_csv(holdings or "", now_pre)

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

    execute_decisions(result, holdings, h_sha, decisions, d_sha)

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

    # 最新 holdings を再読み込みし、summary・positions を再生成
    holdings_fresh, _ = read_file("data/holdings.csv")
    decisions_fresh, _ = read_file("data/trade_decisions.csv")
    h_latest = holdings_fresh or holdings or ""
    d_latest = decisions_fresh or decisions or ""

    # holdings_summary.csv: holdings.csv から毎回完全再生成（正本は holdings.csv）
    summary_new = regenerate_summary_csv(h_latest, now)
    _, s_sha = read_file("data/holdings_summary.csv")
    ok = write_file("data/holdings_summary.csv", summary_new,
                    f"scan({SCAN_TYPE}): summary再生成 {now.strftime('%Y-%m-%d %H:%M JST')}", s_sha)
    print(f"[scan.py] holdings_summary.csv: {'OK' if ok else 'FAILED'}")

    # 保有ポジション.md 更新
    _, p_sha = read_file("保有ポジション_positions.md")
    pos_content = generate_positions_md(h_latest, d_latest, now)
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

    # ウォッチリスト.md 更新（監視継続銘柄の自動追記）
    if result.get("screened_candidates"):
        watchlist_fresh, w_sha_fresh = read_file("ウォッチリスト_watchlist.md")
        new_watchlist = update_watchlist_md(watchlist_fresh, result, now)
        if new_watchlist:
            ok = write_file("ウォッチリスト_watchlist.md", new_watchlist,
                            f"scan({SCAN_TYPE}): ウォッチリスト更新 {now.strftime('%Y-%m-%d %H:%M JST')}", w_sha_fresh)
            print(f"[scan.py] ウォッチリスト更新: {'OK' if ok else 'FAILED'}")

    # 週次レビュー.md 更新（週次スキャンのみ）
    if SCAN_TYPE == "weekly":
        weekly_stats = get_weekly_stats(h_latest, now)
        qualitative = weekly_review_analysis(weekly_stats, journal_tail, now)
        review_section = build_weekly_review_section(weekly_stats, qualitative, now)
        weekly_review_md, wr_sha = read_file("週次レビュー_weekly_review.md")
        wr_md = weekly_review_md or ""
        if "---" in wr_md:
            idx = wr_md.index("---")
            new_wr = wr_md[:idx + 3] + "\n" + review_section + "\n" + wr_md[idx + 3:]
        else:
            new_wr = wr_md + review_section
        ok = write_file("週次レビュー_weekly_review.md", new_wr,
                        f"scan({SCAN_TYPE}): 週次レビュー更新 {now.strftime('%Y-%m-%d %H:%M JST')}", wr_sha)
        print(f"[scan.py] 週次レビュー更新: {'OK' if ok else 'FAILED'}")

# ── 週次レビュー ─────────────────────────────────────────────────────

def get_weekly_stats(holdings_csv, now):
    """直近7日の実現損益・確定取引・現在ポジションを集計する"""
    week_start = (now - datetime.timedelta(days=7)).strftime("%Y-%m-%d")

    all_lines = (holdings_csv or "").strip().split("\n")
    before_csv = "\n".join(
        line for i, line in enumerate(all_lines)
        if i == 0 or (
            line.strip() and
            len(line.split(",")) > 1 and
            line.split(",")[1].strip()[:10] < week_start
        )
    )
    state_before = calculate_positions(before_csv)
    state_now    = calculate_positions(holdings_csv or "")

    weekly_trades = []
    for code, s_now in state_now.items():
        pnl_now    = s_now.get("realized_pnl", 0)
        pnl_before = state_before.get(code, {}).get("realized_pnl", 0)
        if abs(pnl_now - pnl_before) > 0.001:
            weekly_trades.append({
                "code":   code,
                "name":   s_now["name"],
                "market": s_now["market"],
                "pnl":    round(pnl_now - pnl_before, 2),
            })

    active = {c: s for c, s in state_now.items() if s.get("position", 0) > 0}
    return {"week_start": week_start, "weekly_trades": weekly_trades, "active_positions": active}


def weekly_review_analysis(stats, journal_tail, now):
    """週次レビューの定性分析を Claude に依頼する"""
    date_str = now.strftime("%Y-%m-%d")

    jpy_markets = ("東証P", "東証S", "東証G")
    usd_markets = ("米国", "NYSE", "NASDAQ")
    trades = stats["weekly_trades"]
    active = stats["active_positions"]

    trades_text = "\n".join(
        f"- {t['name']}({t['code']}): "
        f"{'＋' if t['pnl'] >= 0 else ''}{t['pnl']:,.1f} "
        f"({'JPY' if t['market'] in jpy_markets else 'USD'})"
        for t in trades
    ) if trades else "今週の確定取引なし"

    positions_text = "\n".join(
        f"- {s['name']}({code}) {s['market']}: "
        f"{int(s['position'])}株 @ {round(s['avg_cost'], 0):,.0f}"
        for code, s in active.items()
    ) if active else "保有なし"

    prompt = f"""あなたは株式売買サポートシステムのClaudeです。
実行日時: {date_str} JST（週次レビュー 金曜 16:00）

## 今週の集計
集計期間: {stats['week_start']} 〜 {date_str}

### 確定損益
{trades_text}

### 週末時点の保有ポジション
{positions_text}

### 直近のトレード日誌
{journal_tail}

## 依頼
今週のトレードを振り返り、以下のJSONのみ返してください。
確定取引がない場合はポジション管理・地合い観察の観点で分析してください。

{{
  "plus_factors": ["今週うまくいった判断・要因（最大3項目）"],
  "minus_factors": ["今週のマイナス要因・反省点（最大3項目）"],
  "learnings": ["来週に活かす具体的な改善アクション（1〜3項目）"],
  "next_week_focus": "来週の重点テーマ・注目セクター（1〜2行）"
}}

JSONのみ返してください（前後に説明文不要）。
"""
    print("[scan.py] 週次レビュー分析をClaudeに依頼中...")
    response_text = call_claude(prompt, max_tokens=2000)

    try:
        json_match = re.search(r'\{.*\}', response_text, re.DOTALL)
        if not json_match:
            raise ValueError("JSONが見つかりません")
        return json.loads(json_match.group())
    except (json.JSONDecodeError, ValueError) as e:
        print(f"[scan.py] WARNING: 週次レビューJSONパースエラー: {e}")
        return {
            "plus_factors": ["(分析エラー)"],
            "minus_factors": ["(分析エラー)"],
            "learnings": ["(分析エラー)"],
            "next_week_focus": "(分析エラー)",
        }


def build_weekly_review_section(stats, qualitative, now):
    """週次レビューの Markdown セクションを生成する"""
    week_str   = now.strftime("%Y-%m-%d")
    week_start = stats["week_start"]
    trades     = stats["weekly_trades"]
    active     = stats["active_positions"]

    jpy_markets = ("東証P", "東証S", "東証G")
    usd_markets = ("米国", "NYSE", "NASDAQ")
    jpy_trades  = [t for t in trades if t["market"] in jpy_markets]
    usd_trades  = [t for t in trades if t["market"] in usd_markets]
    jpy_pnl     = sum(t["pnl"] for t in jpy_trades)
    usd_pnl     = sum(t["pnl"] for t in usd_trades)
    jpy_wins    = sum(1 for t in jpy_trades if t["pnl"] > 0)
    usd_wins    = sum(1 for t in usd_trades if t["pnl"] > 0)

    def rate(wins, total):
        return f"{wins}/{total}件" if total else "−"

    sell_rows = "\n".join(
        f"| {t['name']} | {t['code']} | {t['market']} "
        f"| {'＋' if t['pnl'] >= 0 else ''}{t['pnl']:,.1f} |"
        for t in trades
    ) if trades else "| （今週の確定取引なし） | | | |"

    pos_rows = "\n".join(
        f"| {s['name']} | {code} | {s['market']} "
        f"| {int(s['position'])} | {round(s['avg_cost'], 0):,.0f} | — |"
        for code, s in active.items()
    ) if active else "| （保有なし） | | | | | |"

    plus   = "\n".join(f"- {p}" for p in qualitative.get("plus_factors", ["（特になし）"]))
    minus  = "\n".join(f"- {m}" for m in qualitative.get("minus_factors", ["（特になし）"]))
    learns = "\n".join(f"- {l}" for l in qualitative.get("learnings", ["（なし）"]))
    focus  = qualitative.get("next_week_focus", "")

    return f"""

## {week_str}（週次レビュー）
集計期間: {week_start} 〜 {week_str}

### 損益サマリー
| 通貨 | 実現損益 | 確定数 | 勝ち | 勝率 |
|---|---|---|---|---|
| JPY | ¥{jpy_pnl:+,.1f} | {len(jpy_trades)}件 | {jpy_wins}件 | {rate(jpy_wins, len(jpy_trades))} |
| USD | ${usd_pnl:+,.1f} | {len(usd_trades)}件 | {usd_wins}件 | {rate(usd_wins, len(usd_trades))} |

#### 今週の確定取引
| 銘柄 | コード | 市場 | 実現損益 |
|---|---|---|---|
{sell_rows}

### 保有ポジション（週末時点）
| 銘柄 | コード | 市場 | 株数 | 平均取得 | 含み損益 |
|---|---|---|---|---|---|
{pos_rows}
> ※含み損益はリアルタイム非対応のため「—」。

### ◎ プラス要因
{plus}

### × マイナス要因
{minus}

### → 学び・次週の改善アクション
{learns}

**来週の重点テーマ**: {focus}
"""

if __name__ == "__main__":
    main()
