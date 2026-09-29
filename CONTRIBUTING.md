# Development notes

Use a separate virtual environment and run the checks in the README before committing. Keep tests independent of real provider credentials; live model calls are a separate, explicit check.

Preserve these behaviours:

- Tk widgets are only accessed on the main thread; provider and tool work runs in workers.
- Private channel history and memory stay scoped to the assigned provider/model.
- File changes require a concrete preview and host approval, with revalidation before writing.
- Timeouts bound the complete provider operation, including any retry.
- Provider failures are visible and never replaced with mock output.
- Credentials, personal settings, conversations, logs, model weights and working files remain untracked.

This repository contains source and synthetic test fixtures only. Check `git diff --cached` before pushing, and never use `git add -f` to bypass the local-data exclusions.
