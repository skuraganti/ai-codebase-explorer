# Release status

**Local release gates: verified on 2026-10-16.** Source, native tests, Docker workflow/persistence, Pages browser/mobile/error states and publication privacy review passed. Actual publication is recorded by GitHub commit/deployment metadata after this prepared initial commit. See [VERIFICATION.md](VERIFICATION.md) for exact checks and limits.

Local: `sh stack.sh up` → http://127.0.0.1:8284. [Pages demo](https://skuraganti.github.io/ai-codebase-explorer/) · [Source](https://github.com/skuraganti/ai-codebase-explorer). Pages uses static browser interactions; full Python services run locally. Runtime databases, caches, model-control files and credentials stay excluded. The Pages demo searches saved parser output; it does not execute analyzed code or invoke a model. Static imports omit dynamic behavior. Full-app held-out generation included a rejected citation, and exact quotes do not prove every semantic statement.

Next maintenance action: use the documented setup, verify original-source checksums before updating fixtures, rerun affected tests and retain failed/uncertain evaluation outcomes. No hosted inference or unattended service is supplied by Pages.
