#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
github_tool.py — 常用 GitHub 操作命令行工具（纯标准库，无需 pip 安装）

走 GitHub REST API（api.github.com），绕开本机 git 走代理时 github.com 502 的问题。

------------------------------------------------------------
一、配置 Token（二选一，脚本不会把 Token 写进自身文件）
------------------------------------------------------------
方式 A：环境变量
    # Git Bash / macOS / Linux
    export GITHUB_TOKEN=你的token
    # Windows PowerShell
    $env:GITHUB_TOKEN="你的token"

方式 B：把 Token 单独存到一个文件（默认读脚本旁的 .github_token）
    新建文件 .github_token，里面只放一行 Token，然后：
    export GITHUB_TOKEN_FILE=/path/to/.github_token
    建议把 .github_token 加进 .gitignore，别提交上去。

------------------------------------------------------------
二、常用命令（默认 owner=zhangkj123456，repo=YG）
------------------------------------------------------------
    python github_tool.py whoami
    python github_tool.py repos
    python github_tool.py tree
    python github_tool.py tree --path docs
    python github_tool.py cat hello.md
    python github_tool.py put note.md ./local.md --msg "添加笔记"
    python github_tool.py create my-repo --private --desc "说明"
    python github_tool.py commits --limit 5
    python github_tool.py issue "标题" "正文内容"
    python github_tool.py issues

换仓库/账号：任意命令后加 --repo 其他仓库 --owner 其他账号
"""

import os
import sys
import json
import base64
import argparse
import subprocess
import urllib.request
import urllib.error

API = "https://api.github.com"
DEFAULT_OWNER = "zhangkj123456"
DEFAULT_REPO = "YG"
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))


def get_token():
    """按 环境变量 -> Token 文件 的顺序读取，绝不打印明文。"""
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    if token:
        return token
    path = os.environ.get(
        "GITHUB_TOKEN_FILE",
        os.path.join(SCRIPT_DIR, ".github_token"),
    )
    if os.path.isfile(path):
        with open(path, "r", encoding="utf-8") as f:
            value = f.read().strip()
            if value:
                return value
    return None


def require_token():
    token = get_token()
    if not token:
        print(
            "缺少 Token。请设置环境变量 GITHUB_TOKEN，"
            "或把 Token 写入脚本旁的 .github_token 文件。\n"
            "（脚本本身不保存 Token，也不会显示它。）",
            file=sys.stderr,
        )
        sys.exit(1)
    return token


def api(method, path, data=None, token=None):
    """调用 GitHub REST API，返回解析后的 JSON。"""
    url = API + path
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "wb-github-cli",
    }
    if token:
        headers["Authorization"] = "Bearer " + token
    body = None
    if data is not None:
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read().decode("utf-8")
            return json.loads(raw) if raw.strip() else None
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "ignore")[:400]
        raise RuntimeError("GitHub API 错误 HTTP %s: %s" % (e.code, detail)) from e
    except urllib.error.URLError as e:
        raise RuntimeError("网络错误: %s" % e.reason) from e


def owner_repo(args):
    return args.owner, args.repo


def cmd_whoami(args, token):
    me = api("GET", "/user", token=token)
    print("登录名   :", me.get("login"))
    print("主页     :", me.get("html_url"))
    print("公开仓库 :", me.get("public_repos"))
    print("注册时间 :", me.get("created_at", "")[:10])


def cmd_repos(args, token):
    repos = api("GET", "/user/repos?per_page=100&sort=updated", token=token)
    print("我的仓库（共 %d 个）:" % len(repos))
    for r in repos:
        vis = "私有" if r.get("private") else "公开"
        print("  %-28s %-4s %s" % (r["name"], vis, r["html_url"]))


def cmd_tree(args, token):
    owner, repo = owner_repo(args)
    path = (args.path or "").strip("/")
    url = "/repos/%s/%s/contents/%s" % (owner, repo, path)
    items = api("GET", url, token=token)
    if isinstance(items, dict):
        items = [items]
    print("%s/%s/%s 内容:" % (owner, repo, path or "/"))
    for it in items:
        kind = "DIR " if it.get("type") == "dir" else "FILE"
        print("  %-5s %-32s %s" % (kind, it.get("name", ""), it.get("size", "")))


def cmd_cat(args, token):
    owner, repo = owner_repo(args)
    path = args.path.strip("/")
    url = "/repos/%s/%s/contents/%s" % (owner, repo, path)
    item = api("GET", url, token=token)
    if isinstance(item, list):
        print("这是一个目录，请用 tree 查看。")
        return
    content = item.get("content", "")
    try:
        text = base64.b64decode(content).decode("utf-8", "ignore")
    except Exception:
        text = "(二进制文件，无法按文本显示)"
    print(text)


def cmd_put(args, token):
    """创建或更新仓库里的一个文件（等价于一次 commit + push）。"""
    owner, repo = owner_repo(args)
    path = args.path.strip("/")
    if not os.path.isfile(args.local_file):
        print("本地文件不存在:", args.local_file, file=sys.stderr)
        sys.exit(1)
    with open(args.local_file, "rb") as f:
        content = base64.b64encode(f.read()).decode("ascii")

    url = "/repos/%s/%s/contents/%s" % (owner, repo, path)
    payload = {
        "message": args.msg or ("Update %s via github_tool.py" % path),
        "content": content,
    }
    # 文件已存在时必须带 sha，否则 API 拒绝更新
    try:
        existing = api("GET", url, token=token)
        if isinstance(existing, dict) and existing.get("sha"):
            payload["sha"] = existing["sha"]
    except RuntimeError as e:
        if "HTTP 404" not in str(e):
            raise
    result = api("PUT", url, data=payload, token=token)
    info = result.get("content", {})
    print("推送成功: %s" % info.get("html_url", path))
    print("提交信息: %s" % result.get("commit", {}).get("message", ""))


def cmd_create(args, token):
    payload = {
        "name": args.name,
        "private": args.private,
        "description": args.desc or "",
        "auto_init": True,
    }
    repo = api("POST", "/user/repos", data=payload, token=token)
    print("仓库创建成功:", repo.get("html_url"))


def cmd_commits(args, token):
    owner, repo = owner_repo(args)
    commits = api(
        "GET",
        "/repos/%s/%s/commits?per_page=%d" % (owner, repo, args.limit),
        token=token,
    )
    print("最近 %d 次提交:" % len(commits))
    for c in commits:
        sha = c.get("sha", "")[:7]
        msg = c.get("commit", {}).get("message", "").splitlines()[0]
        print("  %s  %s" % (sha, msg))


def cmd_issue(args, token):
    owner, repo = owner_repo(args)
    result = api(
        "POST",
        "/repos/%s/%s/issues" % (owner, repo),
        data={"title": args.title, "body": args.body or ""},
        token=token,
    )
    print("Issue 已创建:", result.get("html_url"))


def cmd_issues(args, token):
    owner, repo = owner_repo(args)
    issues = api(
        "GET", "/repos/%s/%s/issues?state=all&per_page=30" % (owner, repo),
        token=token,
    )
    print("Issue 列表（共 %d 条）:" % len(issues))
    for i in issues:
        print("  #%-3d %-6s %s" % (i.get("number", 0), i.get("state", ""), i.get("title", "")))


def cmd_pushdir(args, token):
    """把当前 git 仓库已追踪的文件，作为一次 commit 推到远程分支。"""
    owner, repo = owner_repo(args)
    branch = args.branch

    # 取回远程分支当前状态（不存在则视为全新分支）
    try:
        ref = api("GET", "/repos/%s/%s/git/ref/heads/%s" % (owner, repo, branch), token=token)
        parent_sha = ref["object"]["sha"]
        base_commit = api("GET", "/repos/%s/%s/git/commits/%s" % (owner, repo, parent_sha), token=token)
        base_tree_sha = base_commit["tree"]["sha"]
    except RuntimeError as e:
        if "HTTP 404" not in str(e):
            raise
        parent_sha = None
        base_tree_sha = None

    # 读取本地已追踪文件，逐个创建 blob
    tracked = subprocess.check_output(
        ["git", "ls-files"], text=True, encoding="utf-8"
    ).splitlines()
    files = []
    for path in tracked:
        if not os.path.isfile(path):
            continue
        with open(path, "rb") as f:
            b64 = base64.b64encode(f.read()).decode("ascii")
        blob = api("POST", "/repos/%s/%s/git/blobs" % (owner, repo),
                   data={"content": b64, "encoding": "base64"}, token=token)
        files.append({"path": path, "mode": "100644", "type": "blob", "sha": blob["sha"]})
        print("  已准备:", path)

    # 在远程现有 tree 基础上叠加本地文件，创建 tree → commit → 更新分支引用
    tree_payload = {"tree": files}
    if base_tree_sha:
        tree_payload["base_tree"] = base_tree_sha
    tree = api("POST", "/repos/%s/%s/git/trees" % (owner, repo),
               data=tree_payload, token=token)

    commit_payload = {
        "message": args.msg or "初始化：Git 速查表与 GitHub 工具脚本",
        "tree": tree["sha"],
    }
    if parent_sha:
        commit_payload["parents"] = [parent_sha]
    commit = api("POST", "/repos/%s/%s/git/commits" % (owner, repo),
                 data=commit_payload, token=token)

    if parent_sha:
        api("PATCH", "/repos/%s/%s/git/refs/heads/%s" % (owner, repo, branch),
            data={"sha": commit["sha"], "force": False}, token=token)
        print("推送完成: https://github.com/%s/%s/commit/%s" % (owner, repo, commit["sha"][:7]))
    else:
        api("POST", "/repos/%s/%s/git/refs" % (owner, repo),
            data={"ref": "refs/heads/%s" % branch, "sha": commit["sha"]}, token=token)
        print("新建分支并推送完成: https://github.com/%s/%s/tree/%s" % (owner, repo, branch))


def build_parser():
    p = argparse.ArgumentParser(
        description="常用 GitHub 操作命令行工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    sub = p.add_subparsers(dest="command")

    def common(sp):
        sp.add_argument("--owner", default=DEFAULT_OWNER, help="仓库所有者，默认 %s" % DEFAULT_OWNER)
        sp.add_argument("--repo", default=DEFAULT_REPO, help="仓库名，默认 %s" % DEFAULT_REPO)

    sub.add_parser("whoami", help="查看当前登录账号")

    sub.add_parser("repos", help="列出我的仓库")

    t = sub.add_parser("tree", help="列出仓库目录")
    common(t)
    t.add_argument("--path", default="", help="目录路径，默认根目录")

    c = sub.add_parser("cat", help="读取仓库里某个文件的内容")
    common(c)
    c.add_argument("path", help="仓库内文件路径，如 hello.md")

    u = sub.add_parser("put", help="创建或更新文件（一次 commit + push）")
    common(u)
    u.add_argument("path", help="仓库内目标路径，如 docs/note.md")
    u.add_argument("local_file", help="本地文件路径")
    u.add_argument("--msg", default="", help="提交信息")

    cr = sub.add_parser("create", help="新建仓库")
    cr.add_argument("name", help="仓库名")
    cr.add_argument("--private", action="store_true", help="设为私有，默认公开")
    cr.add_argument("--desc", default="", help="仓库描述")

    cm = sub.add_parser("commits", help="查看提交历史")
    common(cm)
    cm.add_argument("--limit", type=int, default=10, help="条数，默认 10")

    i = sub.add_parser("issue", help="新建 Issue")
    common(i)
    i.add_argument("title", help="标题")
    i.add_argument("body", nargs="?", default="", help="正文（可选）")

    isu = sub.add_parser("issues", help="列出 Issue")
    common(isu)

    pd = sub.add_parser("pushdir", help="把当前仓库已追踪文件作为一次 commit 推到远程分支")
    common(pd)
    pd.add_argument("--branch", default="main", help="目标分支，默认 main")
    pd.add_argument("--msg", default="", help="提交信息")

    return p


HANDLERS = {
    "whoami": cmd_whoami,
    "repos": cmd_repos,
    "tree": cmd_tree,
    "cat": cmd_cat,
    "put": cmd_put,
    "create": cmd_create,
    "commits": cmd_commits,
    "issue": cmd_issue,
    "issues": cmd_issues,
    "pushdir": cmd_pushdir,
}


def main():
    parser = build_parser()
    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        return
    token = require_token()
    try:
        HANDLERS[args.command](args, token)
    except RuntimeError as e:
        print(str(e), file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
