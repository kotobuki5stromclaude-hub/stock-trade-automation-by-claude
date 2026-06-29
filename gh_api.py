import os, sys, json, base64, subprocess, urllib.parse

REPO = "kotobuki5stromclaude-hub/stock-trade-automation-by-claude"
TOKEN = os.environ["GITHUB_TOKEN"]
API = "https://api.github.com"
CA = "/root/.ccr/ca-bundle.crt"

def _req(method, path, body=None):
    url = path if path.startswith("http") else f"{API}{path}"
    cmd = ["curl", "-s", "--cacert", CA, "-X", method,
           "-H", f"Authorization: token {TOKEN}",
           "-H", "Accept: application/vnd.github+json"]
    if body is not None:
        cmd += ["-H", "Content-Type: application/json", "-d", json.dumps(body)]
    cmd.append(url)
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    d = json.loads(result.stdout or "{}")
    status = d.get("status")
    if isinstance(status, str) and status.isdigit():
        return int(status), d
    if "sha" in d or "content" in d or "commit" in d:
        return 200, d
    if "message" in d and d["message"] not in ("", None):
        return 400, d
    return 200, d

def get_file(path, ref="main"):
    _, d = _req("GET", f"/repos/{REPO}/contents/{urllib.parse.quote(path)}?ref={ref}")
    if "content" not in d:
        return None, None
    return base64.b64decode(d["content"]).decode("utf-8"), d["sha"]

def put_file(path, content, message, sha=None, branch="main"):
    body = {"message": message, "content": base64.b64encode(content.encode()).decode(), "branch": branch}
    if sha:
        body["sha"] = sha
    _, d = _req("PUT", f"/repos/{REPO}/contents/{urllib.parse.quote(path)}", body)
    commit_sha = d.get("commit", {}).get("sha") or d.get("message", "ERROR")
    return (201 if "commit" in d else 400), commit_sha

if __name__ == "__main__":
    cmd = sys.argv[1]
    if cmd == "get":
        c, sha = get_file(sys.argv[2])
        print(f"SHA={sha}\n---\n{c}")
    elif cmd == "test":
        path = "トレード日誌_journal.md"
        content, sha = get_file(path)
        if content is None:
            print("READ FAILED"); sys.exit(1)
        addition = f"\n- テスト: curl経由GitHub API接続確認OK\n"
        st, info = put_file(path, content.rstrip() + "\n" + addition, "test: curl-based API write", sha)
        print("PUT status:", st, "commit:", info)
