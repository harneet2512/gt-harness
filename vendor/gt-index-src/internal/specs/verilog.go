package specs

import (
	tree_sitter_verilog "github.com/tree-sitter/tree-sitter-verilog/bindings/go"
	sitter "github.com/smacker/go-tree-sitter"
)

// Verilog modules are the structural units used by Terminal-Bench HDL tasks.
// The grammar intentionally has no named fields for module/function names; the
// parser's existing first-identifier fallback provides names while the module
// instantiation node supplies deterministic call anchors.
func init() {
	Register(&Spec{
		Name:          "verilog",
		Extensions:    []string{".v"},
		Language:      sitter.NewLanguage(tree_sitter_verilog.Language()),
		FunctionNodes: []string{"function_body_declaration", "task_body_declaration"},
		ClassNodes:    []string{"module_declaration"},
		CallNodes:     []string{"module_instantiation", "function_subroutine_call", "tf_call", "method_call"},
		ImportNodes:   []string{"include_compiler_directive"},
		NameField:     "",
		BodyField:     "",
		ParamsField:   "tf_port_list",
		IsExported: func(name string) bool {
			return name != ""
		},
	})
}
