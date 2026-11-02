"""Read-only GitHub scheduler diagnostics; optional transient GH_TOKEN env."""
from __future__ import annotations

import argparse
import datetime
import json
import os
import re
import ssl
import urllib.error
import urllib.request

from .network_security import NoRedirects


def github_get(repo: str, suffix: str):
    headers = {"User-Agent": "TR-opti-schedule-diagnostics",
               "Accept": "application/vnd.github+json"}
    token = os.environ.get("GH_TOKEN")
    if token:
        headers["Authorization"] = "Bearer " + token
    url = f"https://api.github.com/repos/{repo}" + ("/" + suffix if suffix else "")
    request = urllib.request.Request(url, headers=headers)
    opener = urllib.request.build_opener(
        urllib.request.HTTPSHandler(context=ssl.create_default_context()), NoRedirects())
    with opener.open(request, timeout=15) as response:
        raw = response.read(2 * 1024 * 1024 + 1)
    if len(raw) > 2 * 1024 * 1024:
        raise ValueError("GitHub response exceeds limit")
    return json.loads(raw)


def summarize(repo, workflow, dispatches, recent, now=None):
    now = now or datetime.datetime.now(datetime.timezone.utc)
    findings = []
    if repo.get("archived") or repo.get("disabled"):
        findings.append("REPOSITORY_DISABLED")
    if workflow.get("state") != "active":
        findings.append("WORKFLOW_DISABLED")
    dispatched = dispatches.get("workflow_runs", [])
    if not dispatched:
        findings.append("NO_DISPATCH_EVENTS")
    else:
        last = datetime.datetime.fromisoformat(dispatched[0]["created_at"].replace("Z", "+00:00"))
        if (now - last).total_seconds() > 35 * 60:
            findings.append("DISPATCH_OVERDUE")
        if dispatched[0].get("conclusion") == "failure":
            findings.append("DISPATCH_RUN_FAILED")
    return {
        "repository": repo.get("full_name"), "private": repo.get("private"),
        "defaultBranch": repo.get("default_branch"), "workflowState": workflow.get("state"),
        "dispatchRunCount": dispatches.get("total_count", 0),
        "lastDispatchRun": dispatched[0].get("html_url") if dispatched else None,
        "recentEvents": [r.get("event") for r in recent.get("workflow_runs", [])],
        "findings": findings,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True, help="owner/repository")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", args.repo):
        parser.error("repository must be owner/name")
    try:
        repo = github_get(args.repo, "")
        workflow = github_get(args.repo, "actions/workflows/scanner.yml")
        dispatches = github_get(args.repo, "actions/workflows/scanner.yml/runs?event=workflow_dispatch&per_page=5")
        recent = github_get(args.repo, "actions/workflows/scanner.yml/runs?per_page=10")
        result = summarize(repo, workflow, dispatches, recent)
    except urllib.error.HTTPError as exc:
        print(f"GitHub HTTP {exc.code}. Check the repository, workflow and access permissions.")
        print("Private repositories need read access via transient GH_TOKEN; it is never saved.")
        return 2
    except (OSError, ValueError, KeyError) as exc:
        print(f"Schedule diagnostics unavailable: {type(exc).__name__}")
        return 2
    print(json.dumps(result, indent=2) if args.json else result)
    return 1 if result["findings"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
