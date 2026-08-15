package specs

import (
	"github.com/smacker/go-tree-sitter/svelte"
)

func init() {
	Register(&Spec{
		Name:       "svelte",
		Extensions: []string{".svelte"},
		Language:   svelte.GetLanguage(),

		// The vendored svelte grammar parses `<script>` content as `raw_text`;
		// it never emits function_declaration/call_expression, so no
		// definition or call can be extracted. caller_support is False in the
		// host registry until a grammar-aware extraction path is verified on
		// the Linux source-built indexer.
		FunctionNodes: []string{},
		ClassNodes:    []string{},
		CallNodes:     []string{},
		ImportNodes:   []string{},

		NameField:       "name",
		ReturnTypeField: "",
		BodyField:       "body",
		ParamsField:     "parameters",

		IsExported: func(name string) bool {
			return true
		},
	})
}
