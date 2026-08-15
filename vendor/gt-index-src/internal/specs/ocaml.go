package specs

import (
	"github.com/smacker/go-tree-sitter/ocaml"
)

func init() {
	Register(&Spec{
		Name:       "ocaml",
		Extensions: []string{".ml", ".mli"},
		Language:   ocaml.GetLanguage(),

		FunctionNodes: []string{"value_definition", "let_binding"},
		ClassNodes:    []string{"type_definition", "module_definition"},
		CallNodes:     []string{"application_expression"},
		ImportNodes:   []string{"open_statement"},

		// The vendored OCaml grammar exposes no "name" field on
		// value_definition/let_binding; the name lives in a value_name wrapper
		// child. parser.functionNodeName descends to the first value_name
		// (grammar-scoped), so `let target value = ...` yields the bare name
		// "target". Pattern-matching bindings (let (a, b) = ...) without a
		// value_name abstain.
		NameField: "",
		BodyField: "body",

		IsExported: func(name string) bool {
			return true
		},
	})
}
