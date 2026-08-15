# Additional Tree-sitter grammar notices

The generated parser sources in `cobol/` and `scheme/` are pinned from these
MIT-licensed upstream repositories:

- COBOL: `BloopAI/tree-sitter-cobol`, commit `8ba6692cc3c2bded0693d198936c6e26e6501230`.
- Scheme: `thchha/tree-sitter-scheme`, commit `8d95e3608c00d7aaa8a5c36a8fced00d502addde`.

Their original MIT license notices are retained alongside the generated
sources. The grammars are used only for structural parsing; unresolved or
ambiguous references remain absent from the graph.
