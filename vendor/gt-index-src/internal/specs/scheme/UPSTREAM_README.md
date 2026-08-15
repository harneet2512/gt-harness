# tree-sitter grammar for scheme

This is a scheme grammar for tree-sitter which was created by translating the R5RS spec and extended by the R7RS spec.
It should be included in my language server [zamba](https://zamba.hagiga.de) so that it supports Scheme as well.

# highlighting

I do not use tree-sitter-highlighting yet.
Therefore, there are no queries.
Due to my aim to query _language constructs_ it should be easy to provide queries, though.

# R7RS-small support

* identifiers are not extended, yet
* nested block-comments are not supported, yet
* string escape sequences are not recognized, yet.
The rest ist tested and working.

# extensions

gambits internal identifiers are allowed.
gambit allows # in identifiers as well.
gambits keyword-arguments are supported.

# build

```
git clone https://gitlab.com/thchha/tree-sitter-scheme.git
cd tree-sitter-scheme
gcc -std=gnu99 -o libtree-sitter-r7rs.so -I./src src/parser.c --shared -Os -fPIC -lstdc++
su -c "mv libtree-sitter-r7rs.so /usr/local/lib/"
```

# license

MIT License.
