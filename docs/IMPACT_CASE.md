# Case study: who depends on Flask's default JSON serialization?

**Question.** Before proposing a change to `DefaultJSONProvider.dumps`, which files in the pinned Flask snapshot import its module, directly or transitively? What exact statements connect them?

Contour answers this from static import evidence. It does not show which code calls `dumps` at runtime, and it does not estimate breakage. The source is Flask 3.1.3 at commit `22d924701a6ae2e4cd01e9a15bbaf3946094af65` (BSD-3-Clause, notice kept in `examples/corpus/flask/LICENSE.txt`), indexed as data and never imported or run.

## The target

`src/flask/json/provider.py` (file ID `80d2bb1d…6af43e90`, SHA-256 `e62984cd…74b5765c10`) defines `DefaultJSONProvider.dumps` on [lines 166–179](https://github.com/pallets/flask/blob/22d924701a6ae2e4cd01e9a15bbaf3946094af65/src/flask/json/provider.py#L166-L179):

```python
    def dumps(self, obj: t.Any, **kwargs: t.Any) -> str:
        ...
        kwargs.setdefault("default", self.default)
        kwargs.setdefault("ensure_ascii", self.ensure_ascii)
        kwargs.setdefault("sort_keys", self.sort_keys)
        return json.dumps(obj, **kwargs)
```

## Guided review in the local app

1. Follow [local setup](../README.md#run-locally), including `uv sync --frozen`, `uv run python scripts/bootstrap.py`, then `sh stack.sh up`. Under **Find a file**, choose `json/provider.py`.
2. In **Source & symbols**, open `DefaultJSONProvider.dumps` and read lines 166–179 in the pinned source.
3. Open **Change impact**. Set **Maximum hops** to 3, clear **Include type-only imports**, and press **Trace consumers**.
4. Each result lists the import statement for every hop. Clicking evidence selects that consumer file, so reselect `json/provider.py` before tracing again.

## What the trace shows (depth 3, type-only imports excluded)

| Hops | Files |
|---|---|
| 1 | `json/__init__.py`, `sansio/app.py` |
| 2 | `__init__.py`, `app.py`, `debughelpers.py`, `json/tag.py`, `wrappers.py` |
| 3 | `cli.py`, `sessions.py`, `templating.py` |

That is **10** files, all under `src/flask/`. With **Include type-only imports** on, the same trace finds **19**, because files such as `config.py`, `logging.py` and `testing.py` join this depth-three cohort through type-only paths. This does not exclude longer runtime-import paths; for example, `testing.py` imports `cli.py` and `sessions.py`.

**Direct consumers and their exact statements:**
- `sansio/app.py` [lines 26–27](https://github.com/pallets/flask/blob/22d924701a6ae2e4cd01e9a15bbaf3946094af65/src/flask/sansio/app.py#L26-L27): `from ..json.provider import DefaultJSONProvider` and `from ..json.provider import JSONProvider`.
- `json/__init__.py` [line 7](https://github.com/pallets/flask/blob/22d924701a6ae2e4cd01e9a15bbaf3946094af65/src/flask/json/__init__.py#L7): `from .provider import _default`. It imports a different symbol, so this edge is no evidence that it calls `DefaultJSONProvider.dumps`.

**Two-hop examples:**
- `app.py` [line 44](https://github.com/pallets/flask/blob/22d924701a6ae2e4cd01e9a15bbaf3946094af65/src/flask/app.py#L44) `from .sansio.app import App`, then `sansio/app.py` line 26 or 27, then the provider.
- `wrappers.py` [line 10](https://github.com/pallets/flask/blob/22d924701a6ae2e4cd01e9a15bbaf3946094af65/src/flask/wrappers.py#L10) `from . import json`, then `json/__init__.py` line 7, then the provider.

**Cycle:** the provider itself imports `sansio.app` under `TYPE_CHECKING` ([lines 13–16](https://github.com/pallets/flask/blob/22d924701a6ae2e4cd01e9a15bbaf3946094af65/src/flask/json/provider.py#L13-L16)). That forms a cycle, but the target never appears as its own consumer.

## What the graph cannot tell you

- **No configured instance:** `sansio/app.py` [lines 329–340](https://github.com/pallets/flask/blob/22d924701a6ae2e4cd01e9a15bbaf3946094af65/src/flask/sansio/app.py#L329-L340) lets an application replace the provider (`json_provider_class`, or assigning `app.json`).
- **No call path:** `globals.py` [lines 28–30](https://github.com/pallets/flask/blob/22d924701a6ae2e4cd01e9a15bbaf3946094af65/src/flask/globals.py#L28-L30) reaches the app through a `LocalProxy`. Import edges therefore identify neither the provider instance an application uses nor any real call path.
- **What the results are:** a list of source-linked files worth reviewing before a change. They are not a list of files that will break. Runtime behaviour, plugins and code outside the 66 pinned files are out of scope; see [STATIC_BOUNDARIES.md](STATIC_BOUNDARIES.md).

## Pages demo

The static Pages site shows the same 66 files and 257 saved file pairs, and offers **Show reverse impact**. Its saved pairs include type-only imports, and it has no hop or type-only controls, so it cannot reproduce the 10-file cohort above. Use the local app for this review.

`tests/test_case_study.py` checks this case against the pinned corpus: the file and method pins, all three direct import statements across two consumers, exact paths/distances and raw-source statements for the 10-file type-excluded cohort, the 19-file type-inclusive count/superset, and index persistence after an unchanged rescan and reopen.
