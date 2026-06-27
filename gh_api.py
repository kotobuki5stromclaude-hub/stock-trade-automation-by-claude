import os, sys, json, base64, urllib.request, urllib.error

REPO = "kotobuki5stromclaude-hub/stock-trade-automation-by-claude"
TOKEN = os.environ["GITHUB_TOKEN"]
API = "https://api.github.com"

def _req(method, path, body=None):
    url = path if path.startswith("http") else f"{API}{path}"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", f"Bearer {TOKEN}")
    req.add_header("Accept", "application/vnd.github+json")
    if data: req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode() or "{}")

def get_file(path, ref="main"):
    st, d = _req("GET", f"/repos/{REPO}/contents/{urllib.parse.quote(path)}?ref={ref}")
    if st != 200: return None, None
    return base64.b64decode(d["content"]).decode("utf-8"), d["sha"]

def put_file(path, content, message, sha=None, branch="main"):
    body = {"message": message, "content": base64.b64encode(content.encode()).decode(), "branch": branch}
    if sha: body["sha"] = sha
    st, d = _req("PUT", f"/repos/{REPO}/contents/{urllib.parse.quote(path)}", body)
    return st, d.get("commit",{}).get("sha") or d.get("message")

import urllib.parse
if __name__ == "__main__":
    cmd = sys.argv[1]
    if cmd == "get":
        c, sha = get_file(sys.argv[2]); print(f"SHA={sha}\n---\n{c}")
    elif cmd == "test":
        # append a line to the journal
        path = "トレード日誌_journal.md"
        content, sha = get_file(path)
        if content is None:
            print("READ FAILED"); sys.exit(1)
        addition = "\n- 2026-06-27 追記: Claude実行環境からGitHub API経由の読み書き接続を確認(write test OK)。\n"
        new = content.rstrip()+"\n"+addition
        st, info = put_file(path, new, "test: confirm Claude API write access", sha)
        print("PUT status:", st, "commit:", info)
