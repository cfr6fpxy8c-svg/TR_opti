"""Publish an exact data allowlist; credentials exist only in child-process env."""
from __future__ import annotations

import base64
import datetime
import json
import os
from pathlib import Path
import re
import shutil
# Fixed git executable and argument lists; never invoke a shell.
import subprocess  # nosec B404
import time

DATA_NAMES = (
    "signals.json", "performance.json", "system-status.json",
    "market-snapshots.json", "latest-chart.json", "universe-snapshot.json",
    "trend4h-paper.json", "ai-analyst.json", "trend-combo-paper.json", "ai-combo.json",
)
APPROVED = {f"data/{name}" for name in DATA_NAMES} | {
    f"frontend/data/{name}" for name in (*DATA_NAMES, "badge.json")
}


def validated_files(artifact: Path) -> list[str]:
    root = artifact.resolve()
    found = set()
    for path in artifact.rglob("*"):
        if path.is_symlink() or not path.resolve().is_relative_to(root):
            raise ValueError("artifact symlinks are forbidden")
        if not path.is_file():
            continue
        name = path.relative_to(artifact).as_posix()
        if name not in APPROVED or path.stat().st_size > 50 * 1024 * 1024:
            raise ValueError("unexpected artifact path or excessive file size")
        if name.endswith(".json"):
            json.loads(path.read_text(encoding="utf-8"), parse_constant=reject_constant)
        found.add(name)
    if found != APPROVED:
        raise ValueError("artifact is missing required generated files")
    for name in DATA_NAMES:
        if (artifact / "data" / name).read_bytes() != (artifact / "frontend/data" / name).read_bytes():
            raise ValueError("source data and frontend mirror differ")
    return sorted(found)


def reject_constant(value: str):
    raise ValueError(f"non-finite JSON constant: {value}")


def git(*args: str, env=None, check=True):
    # All arguments are supplied by this script; '--' separates file paths.
    executable = shutil.which("git")
    if executable is None:
        raise RuntimeError("git is not installed")
    return subprocess.run(  # nosec B603
        [executable, *args], check=check, env=env, timeout=90,
    )


def main() -> None:
    artifact = Path(".scan-output")
    approved = validated_files(artifact)
    branch = os.environ["DEFAULT_BRANCH"]
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", branch) or branch.startswith("-"):
        raise ValueError("unsupported default branch name")
    token = os.environ.pop("GH_TOKEN")
    root = Path.cwd().resolve()
    for name in approved:
        dest = root / name
        if (dest.is_symlink() or any(p.is_symlink() for p in dest.parents if p != root)
                or not dest.resolve().is_relative_to(root)):
            raise ValueError("unsafe publication destination")
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(artifact / name, dest)
    git("add", "--", *approved)
    if git("diff", "--cached", "--quiet", check=False).returncode == 0:
        print("No generated data changes")
        return
    now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%MZ")
    git("-c", "user.name=github-actions[bot]", "-c",
        "user.email=41898282+github-actions[bot]@users.noreply.github.com",
        "commit", "-m", f"chore(data): scan {now}")
    # Git's temporary environment config does not write the token to disk.
    auth = base64.b64encode(f"x-access-token:{token}".encode()).decode()
    auth_env = dict(os.environ, GIT_CONFIG_COUNT="1",
                    GIT_CONFIG_KEY_0="http.https://github.com/.extraheader",
                    GIT_CONFIG_VALUE_0="AUTHORIZATION: basic " + auth,
                    GIT_TERMINAL_PROMPT="0")
    for attempt in range(3):
        git("pull", "--rebase", "origin", branch, env=auth_env)
        if git("push", "origin", f"HEAD:refs/heads/{branch}", env=auth_env, check=False).returncode == 0:
            return
        time.sleep(attempt + 1)
    raise RuntimeError("data push failed after three attempts")


if __name__ == "__main__":
    main()
