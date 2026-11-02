# Staged GitHub Actions workflows

These two files belong at `.github/workflows/`. They are parked here because
the agent that opened the pull request introducing them cannot create or update
files under `.github/workflows/` without the `workflows` OAuth scope — GitHub
rejects the push outright.

## Install

```bash
mkdir -p .github/workflows && cp ops/github-workflows/*.yml .github/workflows/
git add .github/workflows && git commit -m "ci: install scanner + test workflows"
git push
```

Then set **Settings → Actions → General → Workflow permissions** to
**Read and write**, or `scanner.yml` cannot commit the data it produces.

Verify with `python -m scanner.check_docs`, which accepts either location and
fails if the README's `*/15` claim is not backed by a real cron line.

## What they do

| File | Purpose |
|---|---|
| `scanner.yml` | Runs the scan on a `*/15` cron plus `workflow_dispatch`, commits refreshed data with a rebase-retry, and uses a concurrency group so overlapping runs cannot race. |
| `tests.yml` | Runs pytest, `compileall`, `scanner.validate_data` and `scanner.check_docs` on push and pull request. |

## About the cadence

`schedule` is best-effort. GitHub documents it as subject to delay under load,
and **skipped fires are never retried** — a 09:00 run that slips past 10:00 is
dropped, not queued. Community reports put routine drift at 5–15 minutes and
occasionally beyond 30. Schedules are also disabled automatically after 60 days
without repository activity.

Read the cadence as *approximately* every 15 minutes. `workflow_dispatch` is
kept as the manual recovery path, and re-arms a schedule that has gone dormant.
