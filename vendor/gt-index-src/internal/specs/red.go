package specs

// Redcode has no maintained Tree-sitter grammar in the upstream parser
// inventory.  It is nevertheless structurally parseable for GT's needs: the
// parser adapter records labels and label-targeting control-flow instructions
// without pretending that every opcode is a function call.
func init() {
	Register(&Spec{
		Name:       "red",
		Extensions: []string{".red"},
		IsExported: func(name string) bool { return name != "" },
	})
}
