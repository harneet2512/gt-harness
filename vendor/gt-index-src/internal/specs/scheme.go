package specs

import (
	"github.com/harneet2512/groundtruth/gt-index/internal/specs/scheme"
)

func init() {
	Register(&Spec{
		Name:          "scheme",
		Extensions:    []string{".scm", ".ss"},
		Language:      scheme.GetLanguage(),
		FunctionNodes: []string{"binding_procedure", "binding_variable"},
		CallNodes:     []string{"procedure_call"},
		ImportNodes:   []string{"import_declaration"},
		NameField:     "name",
		BodyField:     "body",
		ParamsField:   "arguments",
		IsExported: func(name string) bool {
			return name != "" && name[0] != '_'
		},
	})
}
