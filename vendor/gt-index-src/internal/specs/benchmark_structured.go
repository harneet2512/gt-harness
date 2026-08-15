package specs

// These languages use bounded in-repository structural adapters. The adapters
// tokenize only grammar-level declarations, imports, and references and
// abstain on unknown syntax; they do not use text search as graph truth.
func init() {
	for _, spec := range []*Spec{
		{Name: "coq", Extensions: []string{".v"}},
		{Name: "stan", Extensions: []string{".stan"}},
		{Name: "sparql", Extensions: []string{".sparql", ".rq"}},
		{Name: "turtle", Extensions: []string{".ttl"}},
		{Name: "latex", Extensions: []string{".tex", ".sty", ".cls"}},
		{Name: "vim", Extensions: []string{".vim"}},
		{Name: "nginx", Extensions: []string{".conf"}},
		{Name: "gcode", Extensions: []string{".gcode", ".nc", ".tap"}},
		{Name: "make", Extensions: []string{".mk"}, Basenames: []string{"Makefile", "makefile", "GNUmakefile"}},
		{Name: "dockerfile", Extensions: []string{".dockerfile"}, Basenames: []string{"Dockerfile", "Containerfile"}},
		{Name: "cmake", Extensions: []string{".cmake"}, Basenames: []string{"CMakeLists.txt"}},
		{Name: "meson", Basenames: []string{"meson.build", "meson_options.txt"}},
		{Name: "autotools", Extensions: []string{".ac", ".am"}, Basenames: []string{"configure.ac", "Makefile.am"}},
		{Name: "objective_c", Extensions: []string{".m", ".mm"}},
	} {
		spec.IsExported = func(name string) bool { return name != "" }
		Register(spec)
	}
}
