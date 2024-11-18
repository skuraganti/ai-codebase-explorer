# Demonstration

For a fresh checkout, first run `uv sync --frozen` and `uv run python scripts/bootstrap.py` as described in [README.md](README.md). Then follow the walkthrough below.

1. Run `sh stack.sh up`; open http://127.0.0.1:8284. Flask and Zod source snapshots are fixed; this application does not execute them.
2. In Flask, search `Flask.dispatch_request` in Symbols. Open its physical source range. Compare Literal source mode for the same qualified name.
3. Open Module atlas. Select a module or use its accessible edge list; arrows point toward imported dependencies. Open an import statement to verify the actual line.
4. Select an imported module, open Change impact, trace one to five hops, and inspect the consumer-to-dependency paths. Toggle type-only imports in Zod. Static reachability does not establish production breakage.
5. Select a definition, ask a focused explanation and click Explain definition. This makes one bounded real cookllm call if the approved local bridge is available and budget remains. Open each quoted source citation. Read the uncertainty statement.
6. Inspect the call ledger and Evaluation notebook. Frozen source tasks, literal baseline, mock tests and actual model checks are reported separately; sample sizes are small.
7. Rescan unchanged pinned sources: revision/evidence should remain. Restart the project's app and verify persisted index/finished ledger entries. Stop with `sh stack.sh stop`; all state remains local.

The stack does not edit code, propose an automatically applied patch, upload a repo, or publish anything. Source licenses/copyrights remain visible in DATA_SOURCES.md and acquired license files.

For a complete source-linked review without model calls, follow the [Flask JSON-provider change-impact case](docs/IMPACT_CASE.md) and [static-analysis boundary examples](docs/STATIC_BOUNDARIES.md). These full-app hop/type controls are separate from Pages’ saved reverse-impact view.
