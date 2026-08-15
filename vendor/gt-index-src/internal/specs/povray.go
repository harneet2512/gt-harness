package specs

// POV-Ray scene files are structured source rather than a general-purpose
// call-graph language.  The parser adapter records macros, declarations, and
// includes; it emits calls only for locally declared macro invocations.
func init() {
	Register(&Spec{
		Name:       "povray",
		Extensions: []string{".pov"},
		IsExported: func(name string) bool { return name != "" },
	})
}
