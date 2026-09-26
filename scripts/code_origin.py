#!/usr/bin/env python3
"""Find which commit / PR / Jira task most likely introduced the failing code.

Reads Bitbucket credentials from BITBUCKET_EMAIL / BITBUCKET_API_TOKEN and the repo
from BITBUCKET_REPO_SLUG. The token never appears in argv, URLs or output: git gets it
through a credential helper that reads the env var at runtime, the API through basic auth.

Output is JSON on stdout. Commit author names, emails and logins are never included:
the only author data is `author_account_id`, the opaque Atlassian account id Bitbucket
reports for the commit, used to fill the Jira "Developer №1" field.

Examples:
  code_origin.py --file app/tasks/sms.py --line 142 --function send_sms --version 2026.09.25.1
  code_origin.py --search "Invalid sender id" --version 2026.09.25.1
"""
import argparse
import base64
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request

WORKSPACE = "cls-gentech"
JIRA_KEY_RE = re.compile(r"JIJI-\d+")
CREDENTIAL_HELPER = (
    '!f(){ echo "username=x-bitbucket-api-token-auth"; '
    'echo "password=$BITBUCKET_API_TOKEN"; }; f'
)
MAX_COMMITS = 5


def git(repo_dir, *args, check=True):
    cmd = ["git", "-c", f"credential.helper={CREDENTIAL_HELPER}"]
    if repo_dir:
        cmd += ["-C", repo_dir]
    res = subprocess.run(cmd + list(args), capture_output=True, text=True)
    if check and res.returncode != 0:
        raise RuntimeError(f"git {args[0]} failed: {res.stderr.strip()[:300]}")
    return res


def ensure_clone(repo_dir, slug):
    if os.path.isdir(os.path.join(repo_dir, ".git")):
        git(repo_dir, "fetch", "--tags", "--quiet", "origin", check=False)
        return
    url = f"https://bitbucket.org/{WORKSPACE}/{slug}.git"
    git(None, "clone", "--quiet", "--filter=blob:none", url, repo_dir)


def resolve_ref(repo_dir, version):
    """Use the Datadog version tag/commit if the repo has it, otherwise the default branch."""
    if version:
        for candidate in (version, f"v{version}", f"refs/tags/{version}"):
            res = git(repo_dir, "rev-parse", "--verify", "--quiet", f"{candidate}^{{commit}}", check=False)
            if res.returncode == 0:
                return candidate, False
    head = git(repo_dir, "symbolic-ref", "--quiet", "refs/remotes/origin/HEAD", check=False).stdout.strip()
    return (head.replace("refs/remotes/", "") if head else "HEAD"), True


def blame_commits(repo_dir, ref, path, line):
    start = max(1, line - 5)
    out = git(repo_dir, "blame", "--porcelain", "-L", f"{start},{line + 5}", ref, "--", path).stdout
    seen, order = {}, []
    current = None
    for row in out.splitlines():
        m = re.match(r"^([0-9a-f]{40}) \d+ (\d+)", row)
        if m:
            current = m.group(1)
            if current not in seen:
                seen[current] = set()
                order.append(current)
            seen[current].add(int(m.group(2)))
    return [
        {"sha": sha, "blamed_lines": sorted(seen[sha]), "touches_failing_line": line in seen[sha]}
        for sha in order
        if not sha.startswith("0000000")
    ]


def log_shas(repo_dir, ref, *args):
    out = git(repo_dir, "log", ref, f"-n{MAX_COMMITS}", "--format=%H", *args, check=False).stdout
    return [s for s in out.split() if re.fullmatch(r"[0-9a-f]{40}", s)]


def grep_locations(repo_dir, ref, text):
    out = git(repo_dir, "grep", "-n", "-F", text, ref, "--", check=False).stdout
    locs = []
    for row in out.splitlines()[:10]:
        parts = row.split(":", 3)  # ref:path:line:content
        if len(parts) >= 3:
            locs.append({"file": parts[1], "line": int(parts[2])})
    return locs


def commit_info(repo_dir, sha):
    # %cs = committer date; author fields are intentionally not requested
    out = git(repo_dir, "show", "-s", "--format=%h%x00%cs%x00%B", sha).stdout
    short, date, message = out.split("\x00", 2)
    return {
        "sha": sha,
        "short_sha": short,
        "date": date,
        "message": message.strip(),
        "jira_keys": sorted(set(JIRA_KEY_RE.findall(message))),
    }


def bitbucket_get(slug, path):
    """GET a Bitbucket API path; returns (data, None) or (None, error string)."""
    email, token = os.environ.get("BITBUCKET_EMAIL"), os.environ.get("BITBUCKET_API_TOKEN")
    url = f"https://api.bitbucket.org/2.0/repositories/{WORKSPACE}/{slug}/{path}"
    req = urllib.request.Request(url)
    req.add_header("Authorization", "Basic " + base64.b64encode(f"{email}:{token}".encode()).decode())
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.load(resp), None
    except urllib.error.HTTPError as e:
        return None, f"HTTP {e.code}"
    except urllib.error.URLError as e:
        return None, str(e.reason)[:200]


def bitbucket_author(slug, sha):
    """Only the Atlassian account id of the commit author; raw/display_name/nickname are dropped."""
    data, err = bitbucket_get(slug, f"commit/{sha}")
    if err:
        return {"author_account_id": None, "author_error": err}
    user = (data.get("author") or {}).get("user") or {}
    if not user.get("account_id"):
        return {"author_account_id": None, "author_error": "commit author is not linked to an Atlassian account"}
    return {"author_account_id": user["account_id"]}


def bitbucket_prs(slug, sha):
    data, err = bitbucket_get(slug, f"commit/{sha}/pullrequests")
    if err:
        return {"error": err}
    prs = []
    for pr in data.get("values", []):
        branch = ((pr.get("source") or {}).get("branch") or {}).get("name", "")
        title = pr.get("title", "")
        prs.append({
            "id": pr.get("id"),
            "title": title,
            "state": pr.get("state"),
            "branch": branch,
            "url": ((pr.get("links") or {}).get("html") or {}).get("href"),
            "jira_keys": sorted(set(JIRA_KEY_RE.findall(f"{branch} {title}"))),
        })
    return prs


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--file", help="path of the deepest project frame")
    p.add_argument("--line", type=int, help="line number of that frame")
    p.add_argument("--function", help="function name for git log -L")
    p.add_argument("--search", help="unique error string for git grep / git log -S")
    p.add_argument("--version", help="Datadog version tag")
    p.add_argument("--repo-dir", default="/tmp/backend")
    p.add_argument("--no-clone", action="store_true", help="use --repo-dir as is (tests)")
    p.add_argument("--no-api", action="store_true", help="skip Bitbucket PR and author lookup")
    a = p.parse_args()

    if not (a.file and a.line) and not a.search:
        p.error("give --file and --line, or --search")

    slug = os.environ.get("BITBUCKET_REPO_SLUG", "")
    result = {"slug": slug}
    if not a.no_clone:
        missing = [v for v in ("BITBUCKET_REPO_SLUG", "BITBUCKET_EMAIL", "BITBUCKET_API_TOKEN") if not os.environ.get(v)]
        if missing:
            print(json.dumps({"error": "no_access", "detail": f"missing env: {', '.join(missing)}"}))
            return 0
        try:
            ensure_clone(a.repo_dir, slug)
        except RuntimeError as e:
            print(json.dumps({"error": "no_access", "detail": str(e)}))
            return 0

    ref, fallback = resolve_ref(a.repo_dir, a.version)
    result.update({"ref_used": ref, "ref_fallback": fallback, "requested_version": a.version})

    candidates = {}  # sha -> evidence

    def add(sha, source, **extra):
        ev = candidates.setdefault(sha, {"sources": []})
        ev["sources"].append(source)
        ev.update(extra)

    if a.search:
        result["grep_locations"] = grep_locations(a.repo_dir, ref, a.search)
        for sha in log_shas(a.repo_dir, ref, "-S", a.search):
            add(sha, "log -S")
        if not (a.file and a.line) and result["grep_locations"]:
            loc = result["grep_locations"][0]
            a.file, a.line = loc["file"], loc["line"]

    if a.file and a.line:
        result["location"] = {"file": a.file, "line": a.line, "function": a.function}
        try:
            for b in blame_commits(a.repo_dir, ref, a.file, a.line):
                add(b.pop("sha"), "blame", **b)
        except RuntimeError as e:
            result["blame_error"] = str(e)
        if a.function:
            for sha in log_shas(a.repo_dir, ref, "--no-patch", f"-L:{a.function}:{a.file}"):
                add(sha, "log -L")

    commits = []
    for sha, ev in candidates.items():
        info = commit_info(a.repo_dir, sha)
        info.update(ev)
        if slug and not a.no_api:
            info["commit_url"] = f"https://bitbucket.org/{WORKSPACE}/{slug}/commits/{sha}"
            info["pull_requests"] = bitbucket_prs(slug, sha)
            info.update(bitbucket_author(slug, sha))
        commits.append(info)
    # Commits that touched the failing line first, then newest first.
    commits.sort(key=lambda c: c["date"], reverse=True)
    commits.sort(key=lambda c: c.get("touches_failing_line", False), reverse=True)
    result["commits"] = commits
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
