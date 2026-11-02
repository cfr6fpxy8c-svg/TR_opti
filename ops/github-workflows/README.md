# GitHub workflows

The authoritative workflows are in `.github/workflows/`. Obsolete staged
copies were removed to avoid restoring an old cron, broad token permissions
and unpinned Actions.

Follow the root README. A writer must push the changed cron to the default
branch to register it. A manual run does not verify or reactivate scheduled
delivery: check for an execution whose event is `schedule`.

Only the isolated publication job receives `contents: write`; keep default
workflow permissions read-only. Repository visibility and billing remain
GitHub account settings.
