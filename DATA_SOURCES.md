# Data sources — Contour

Actual development and source retrieval: 2026-10-16. The 2024 prefix is a technology theme. All analyzed code is inert data: no upstream script, build, test suite or package installer runs.

## Selection

| Candidate | Provenance and permission | Fit / tradeoff | Decision |
|---|---|---|---|
| Flask 3.1.3 + Zod 4.6.7 | Original Pallets / Colin McDonnell and upstream contributors; BSD-3-Clause / MIT | Framework dispatch and validation internals; Python package hierarchy plus TS re-exports/type-only imports; bounded real complexity | Selected |
| Rich + Ink | Original Textualize / Vadim Demedes and contributors; MIT | Useful terminal architecture, but TSX/rendering and terminal behavior add scope outside source-only module analysis | Compared, not acquired |
| Starlette + Zod | Original Kludex / Zod publishers; BSD-3-Clause / MIT | Strong async framework alternative; Flask adds contrasting architecture to this application's FastAPI/Starlette runtime | Compared, not acquired |

This is a design choice, not a measured ranking. Sources: [Flask release](https://github.com/pallets/flask/releases/tag/3.1.3), [Zod release](https://github.com/colinhacks/zod/releases/tag/v4.6.7), [Rich license](https://raw.githubusercontent.com/Textualize/rich/master/LICENSE), [Ink license](https://raw.githubusercontent.com/vadimdemedes/ink/master/license), [Starlette license](https://raw.githubusercontent.com/Kludex/starlette/main/LICENSE.md). No login or paid access used.

Flask's changelog says released 2026-02-18; GitHub publication is 2026-02-19T05:01:30Z. Zod's GitHub publication is 2026-10-12T16:30:32Z. Acquisition resolves tags to the pinned commits and fails if a tag moves.

## Acquired snapshots

### pallets/flask / 3.1.3

- Original publisher: Pallets and Flask contributors.
- Commit: `22d924701a6ae2e4cd01e9a15bbaf3946094af65`.
- Archive: [flask-22d924701a6ae2e4cd01e9a15bbaf3946094af65.tar.gz](https://codeload.github.com/pallets/flask/tar.gz/22d924701a6ae2e4cd01e9a15bbaf3946094af65); 770,816 bytes.
- SHA256: `f745d5ccfc1e5c4ccb7f543c501936c64b5ae922ef6153626b5e26ab799037cb`.
- Retrieved: 2026-10-16T10:12:31.040860+00:00.
- License: BSD-3-Clause, exact upstream notice retained at `data/corpus/flask/LICENSE.txt`.
- Selection: 24 UTF-8 source files; 337,446 bytes, under `src/flask/`.

### colinhacks/zod / v4.6.7

- Original publisher: Colin McDonnell and Zod contributors.
- Commit: `b500a2ee95134f81d74da7911d7378dd2853d042`.
- Archive: [zod-b500a2ee95134f81d74da7911d7378dd2853d042.tar.gz](https://codeload.github.com/colinhacks/zod/tar.gz/b500a2ee95134f81d74da7911d7378dd2853d042); 10,434,679 bytes.
- SHA256: `ac25c3bcd722faabc689bb86e12f94de72ad36e70d496cafbcff09b120f60be4`.
- Retrieved: 2026-10-16T10:12:31.948871+00:00.
- License: MIT, exact upstream notice retained at `data/corpus/zod/LICENSE`.
- Selection: 42 UTF-8 source files; 830,369 bytes, under `packages/zod/src/v4/core/`, `packages/zod/src/v4/classic/`, `packages/zod/src/v4/mini/`.

## Preparation, schema and limitations

`uv run python scripts/acquire.py` fetches only anonymous original GitHub tag/ref and codeload endpoints with bounded requests. For a second acquisition use `--output-dir data/reproduction`; existing manifests are preserved. Tags must resolve to the recorded commit. Archive selection reads regular known-prefix entries manually, rejects links/oversize files, never uses extractall and never executes data. Raw archive SHA256, original per-file bytes/SHA256/source URL, retrieval headers and selection are in `data/raw/manifest.json` and `data/corpus/manifest.json`. Both are identical after initial acquisition. No text cleaning, newline conversion, generated labels or sampling within selected prefixes. License text is copied separately from source.

There are 66 files / 1,167,815 source bytes. This is a bounded architecture sample, not the entire installed packages. Flask includes 24 runtime Python files. Zod includes 42 .ts files only under v4/core, classic, mini: locales, v3, tests, benchmarks, docs and other entrypoints are excluded. A re-export into locales is **outside_sample**, not a broken dependency. Runtime conditionals, plugin loading, dynamic dispatch, third-party dependencies and type inference are not established by static import reachability. Contributions and naming reflect these two libraries; no general parser accuracy claim follows.

Symbols use file IDs SHA256(repo_id + ':' + path), stable definition IDs, inclusive physical 1-based line ranges and exact source text. Imports retain original statement/range, status, type-only flag and resolved target when supported. Graph edges point from importer to imported module. Impact traverses reverse edges with a depth cap; a path is potential static reachability, not demonstrated runtime breakage. Parsing diagnostics remain visible.

The copyrights/notices/disclaimers must accompany reused source. BSD also forbids implying endorsement. This portfolio credits upstream authors; original work is the index, graph/impact analysis, evidence protocol, interface and evaluations. No claim that imported source was written for this portfolio. No employer/private repository, credential or applicant data was acquired.

## Evaluation splits

`evals/heldout.json` was frozen before endpoint measurement and excluded from the Claude implementation worker. Twelve manually selected source tasks: four development, eight held out. Python ranges use independent stdlib AST; TS anchors were read in original files. Same queries and repo scope compare symbol-aware search with literal source-line search. Includes qualified-name tasks and a no-match task; not a random representative benchmark. Four separate frozen model checks include one development explanation, two held-out explanations and one synthetic injection/unknown request. Synthetic prompts are labeled separately from the real repository evidence. No source-specific held-out retuning. Reports include sample sizes, latency methodology, machine/runtime versions and failures; mocks are separate.

## Browser component

Cytoscape.js 3.34.4 (MIT, Cytoscape Consortium) renders the graph. [Official documentation](https://js.cytoscape.org/) and [npm pinned tarball](https://registry.npmjs.org/cytoscape/-/cytoscape-3.34.4.tgz). `scripts/vendor.py` verifies published SHA512 integrity, bounds archive size, and copies only dist/cytoscape.min.js and LICENSE. Exact hashes/date are `app/static/vendor/manifest.json`. No package scripts run, no runtime CDN requests. Source-index logic is original and independent of Cytoscape.

## Official TypeScript parser component

Microsoft TypeScript6.0.3 (Apache-2.0), [pinned npm archive](https://registry.npmjs.org/typescript/-/typescript-6.0.3.tgz), [official Compiler API reference](https://github.com/microsoft/TypeScript/wiki/Using-the-Compiler-API). Acquired2026-10-16; archiveSHA256 `33cd0ee1beaa8c9e9d15a9da836c62ddea4c34a42d7c2d349dbc80d94165d22a`. scripts/vendor_typescript.py verifies publishedSHA512 and copies only lib/typescript.js,LICENSE.txt,ThirdPartyNoticeText.txt. app/ts_vendor/manifest.json records exact file sizes/hashes. This is a trusted parsing dependency, separate from the analyzed Flask/Zod data. No npm scripts run. The native tree-sitter candidate was removed after observed crash/syntax failures; those failures are retained in DECISIONS.md.
