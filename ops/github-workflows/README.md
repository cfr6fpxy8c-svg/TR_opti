# GitHub workflows

The authoritative workflows are in `.github/workflows/`. Obsolete staged
copies were removed to avoid restoring an old cron, broad token permissions
and unpinned Actions.

Follow the root README. The scanner is dispatched every 15 minutes by an
external cron through the `workflow_dispatch` API (`source=cron`); GitHub's
built-in schedule is not used for it.

Only the isolated publication job receives `contents: write`; keep default
workflow permissions read-only. Repository visibility and billing remain
GitHub account settings.
