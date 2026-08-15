package specs

import (
	"github.com/smacker/go-tree-sitter/lua"
)

func init() {
	Register(&Spec{
		Name:       "lua",
		Extensions: []string{".lua"},
		Language:   lua.GetLanguage(),

		// The vendored lua grammar (smacker/go-tree-sitter/lua) emits
		// `function_statement`/`function_name` with NO named fields; the
		// FunctionNodes below reference node types that do not exist, so
		// definitions were never extracted. caller_support is False in the host
		// registry until grammar-aware extraction is verified on the Linux
		// source-built indexer. CallNodes `function_call` is valid and kept for
		// call-site observation/ranking.
		FunctionNodes: []string{},
		ClassNodes:    []string{},
		CallNodes:     []string{"function_call"},
		ImportNodes:   []string{"function_call"},

		NameField:   "name",
		BodyField:   "body",
		ParamsField: "parameters",

		IsExported: func(name string) bool {
			return true
		},
	})
}
