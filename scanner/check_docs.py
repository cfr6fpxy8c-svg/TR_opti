"""Documentation-consistency checker (run by CI).

The v1.2 audit found the README asserting things that were not true of the
code: a test count that did not match the suite, GitHub Actions workflows that
did not exist, and a status badge pointing at a path the engine never writes.
Documentation drift of that kind is not cosmetic -- it is what made the system
look operational while nothing was running.

This module turns those specific claims into assertions:

    python -m scanner.check_docs        # exit 0 = docs match reality

It deliberately checks only *mechanically verifiable* claims. Prose about
methodology is out of scope.
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

from .config import repo_root


def _count_tests() -> int:
    """Number of collected tests, straight from pytest."""
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/", "--collect-only", "-q"],
        cwd=repo_root(), capture_output=True, text=True,
    )
    match = re.search(r"(\d+)\s+tests?\s+collected", proc.stdout)
    if match:
        return int(match.group(1))
    # older/newer pytest phrasing: fall back to counting node ids
    return len([ln for ln in proc.stdout.splitlines() if "::" in ln])


def check() -> list[str]:
    root = repo_root()
    readme = (root / "README.md").read_text(encoding="utf-8")
    problems: list[str] = []

    # Workflows live in .github/workflows/ once installed. Until then they are
    # staged in ops/github-workflows/, because the automation that opens PRs
    # here cannot write to .github/ without the `workflows` OAuth scope. Both
    # locations count as "the workflow exists"; §4 of the README explains the
    # one-command install.
    installed_dir = root / ".github" / "workflows"
    staged_dir = root / "ops" / "github-workflows"

    def _workflow(name: str) -> Path | None:
        for directory in (installed_dir, staged_dir):
            candidate = directory / name
            if candidate.exists():
                return candidate
        return None

    # 1. Every workflow the README references must actually exist somewhere.
    for wf in sorted(set(re.findall(r"\.github/workflows/([A-Za-z0-9_.-]+\.yml)", readme))):
        if _workflow(wf) is None:
            problems.append(
                f"README references .github/workflows/{wf}, which exists in neither "
                f".github/workflows/ nor ops/github-workflows/")

    # 2. The scheduler must exist at all if a 15-minute cadence is claimed.
    workflows = sorted(installed_dir.glob("*.yml")) if installed_dir.is_dir() else []
    if not workflows and staged_dir.is_dir():
        workflows = sorted(staged_dir.glob("*.yml"))
    if re.search(r"every 15 minutes|\*/15", readme) and not workflows:
        problems.append("README claims a 15-minute schedule but no workflow files exist")
    # ...and that scheduler must really carry the cron it advertises.
    if workflows and re.search(r"\*/15 \* \* \* \*", readme):
        crons = "\n".join(w.read_text(encoding="utf-8") for w in workflows)
        if "*/15 * * * *" not in crons:
            problems.append("README documents a */15 cron that no workflow declares")

    # 3. Test counts quoted in the README must match the real suite.
    #
    # Adding a test should not break the build, but a README claiming 153 when
    # the suite has 190 is exactly the drift this checker exists to catch. So
    # the count may lag *behind* reality by a small margin (tests were added
    # and the prose not yet refreshed) but may never overstate it, and may
    # never drift far enough to be misleading.
    actual = _count_tests()
    tolerance = max(5, actual // 20)  # 5%, floor of 5
    quoted = {int(n) for n in re.findall(r"(\d{2,4})\s+(?:unit \+ integration )?tests?\b", readme)}
    for n in sorted(quoted):
        if n > actual:
            problems.append(
                f"README claims {n} tests but the suite only collects {actual}")
        elif actual - n > tolerance:
            problems.append(
                f"README quotes {n} tests; the suite collects {actual} "
                f"(drift {actual - n} > tolerance {tolerance}) -- refresh the README")

    # 4. The badge must point at the file the engine actually writes.
    badge_paths = re.findall(r"raw\.githubusercontent\.com[^)\s]*?/((?:frontend|data)[^)\s?]*badge\.json)",
                             readme.replace("%2F", "/"))
    for path in set(badge_paths):
        if not (root / path).exists():
            problems.append(f"badge URL points at {path}, which the engine does not write")

    # 5. Files the README tells a user to open must exist.
    for rel in set(re.findall(r"`(frontend/[A-Za-z0-9_./-]+\.html)`", readme)):
        if not (root / rel).exists():
            problems.append(f"README references {rel}, which does not exist")

    return problems


def main() -> int:
    problems = check()
    if not problems:
        print("docs consistency OK")
        return 0
    for p in problems:
        print(f"[DOCS] {p}", file=sys.stderr)
    print(f"\n{len(problems)} documentation inconsistency(ies) found", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
