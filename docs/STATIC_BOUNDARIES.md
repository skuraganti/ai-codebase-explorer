# Static boundaries: what an import record does and does not mean

Contour parses Python with the standard-library `ast` and TypeScript with the pinned official TypeScript compiler API. It records import statements; it never executes, builds or installs the analyzed code. Examples cite Flask 3.1.3 (`22d9247…`, BSD-3-Clause) and Zod 4.6.7 (`b500a2e…`, MIT), whose notices are kept with the corpus.

## Import status labels

- **`resolved`** — an in-sample target file. For example, Zod `core/parse.ts` [line 3](https://github.com/colinhacks/zod/blob/b500a2ee95134f81d74da7911d7378dd2853d042/packages/zod/src/v4/core/parse.ts#L3) `import type * as schemas from "./schemas.js"` is resolved and marked **type-only**.
- **`external`** — a package outside the sample. For example, Flask `json/provider.py` line 11 `from werkzeug.http import http_date`.
- **`outside_sample`** — a relative import whose target is not among the parsed files. Three Zod lines point into `locales/`, which was deliberately left out of the 42 selected TypeScript files:
  - `core/index.ts` line 11;
  - `classic/external.ts` line 43;
  - `classic/schemas.ts` line 8.
- **`dynamic`** — a recognized TypeScript `import()` call. It is labelled but never resolved. The snapshot has no such call; this behaviour is shown with a synthetic test fixture only.

## Type-only filtering works per import, not per file pair

Zod `classic/external.ts` re-exports `core/index.ts` several times:
- lines 8 and 10 are type-only (`export type { … }`);
- line 1 (`export * as core`) and lines 11–36 are runtime or mixed.

With **Include type-only imports** cleared, the file pair stays, carried by lines 1 and 11. A pair disappears only when every import in it is type-only.

## Things the index does not record

- **Python `__import__`:** the Python visitor records only `import` and `from … import` statements. Flask `cli.py` [line 245](https://github.com/pallets/flask/blob/22d924701a6ae2e4cd01e9a15bbaf3946094af65/src/flask/cli.py#L245) `__import__(module_name)` therefore produces no record at all, not even a dynamic one.
- **Syntax versus correctness:** the TypeScript parser reports syntax; it does not check type names or runtime correctness.

## Failures stay visible

- **Syntax errors:** a file with a syntax error is still indexed and can be inspected, with its raw source and parser diagnostics. It contributes no definitions and no outgoing imports. The input file is never modified.
- **Hash or manifest failures:** a hash or manifest mismatch is refused (`POST /api/index` returns 422). The last good index, its revision and its counts stay in place, with the error shown in state.

## Caveat: imports pointing at a file that failed to parse

Only files that parsed successfully are resolution targets, so imports aimed at an unparsed module are mislabelled. With a synthetic package whose `broken.py` fails to parse:
- `from .broken import oops` reads **`outside_sample`**;
- `from . import broken` resolves to the package `__init__.py`;
- `import pkg.broken` reads **`external`**.

`outside_sample` and `external` therefore do not always prove a file was physically excluded. Check the file list and parse status before relying on them.

`tests/test_case_study.py` checks each boundary above, against the pinned snapshot or, where the snapshot has no example, a labelled synthetic fixture.
