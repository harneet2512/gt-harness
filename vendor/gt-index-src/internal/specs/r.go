package specs

import (
	tree_sitter_r "github.com/r-lib/tree-sitter-r/bindings/go"
	sitter "github.com/smacker/go-tree-sitter"
)

// R is a validation-relevant Terminal-Bench language. The upstream grammar
// represents an assignment-bound function as a binary_operator whose lhs is
// the concrete identifier and rhs is the function_definition. The parser
// unwraps that structural parent boundary; it never guesses a name from text.
func init() {
	Register(&Spec{
		Name:          "r",
		Extensions:    []string{".r"},
		Language:      sitter.NewLanguage(tree_sitter_r.Language()),
		FunctionNodes: []string{"function_definition"},
		CallNodes:     []string{"call"},
		ImportNodes:   []string{"namespace_definition", "library_call"},
		NameField:     "name",
		BodyField:     "body",
		ParamsField:   "parameters",
		IsExported: func(name string) bool {
			return name != "" && name[0] != '.'
		},
	})
}
