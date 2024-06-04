# Contour browser demonstration

Static browser demonstration of saved source, symbol and graph data; the full Python/Docker application and local startup instructions are in the [source repository](https://github.com/skuraganti/2024-ai-codebase-explorer). Serve the Pages root or `docs` directory with a static HTTP server.

The pinned original Flask and Zod source and actual saved Python AST / TypeScript compiler output are browsable. Language and symbol search operate only on saved names. Reverse impact uses breadth-first traversal of supplied exact resolved edges; it does not guess missing resolution or execute code. Dynamic/unknown imports and runtime behavior are outside scope. The diagram shows a bounded subset; the table carries the full saved resolved edge set. Optional AI features in the full app are local and are not available on Pages.

Pinned repository tags, commits, licenses and file links are displayed in the app. License texts are provided as `flask-LICENSE.txt` and `zod-LICENSE`.
