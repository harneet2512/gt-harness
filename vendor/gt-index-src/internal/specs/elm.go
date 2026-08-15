package specs

import (
	"github.com/smacker/go-tree-sitter/elm"
)

func init() {
	Register(&Spec{
		Name:       "elm",
		Extensions: []string{".elm"},
		Language:   elm.GetLanguage(),

		FunctionNodes: []string{"value_declaration"},
		ClassNodes:    []string{"type_declaration", "type_alias_declaration"},
		CallNodes:     []string{"function_call_expr"},
		ImportNodes:   []string{"import_clause"},

		NameField:       "",
		ReturnTypeField: "",
		// The vendored tree-sitter-elm grammar exposes a `body` field on
		// value_declaration. An empty BodyField made childByFieldOrType return
		// nil, so extractCalls never ran and elm produced zero CALLS edges
		// despite valid definitions.
		BodyField:   "body",
		ParamsField: "",

		IsExported: func(name string) bool {
			return true
		},
	})
}
