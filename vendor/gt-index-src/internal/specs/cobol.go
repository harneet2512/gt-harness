package specs

import (
	"github.com/harneet2512/groundtruth/gt-index/internal/specs/cobol"
)

func init() {
	Register(&Spec{
		Name:          "cobol",
		Extensions:    []string{".cob", ".cbl", ".cpy"},
		Language:      cobol.GetLanguage(),
		FunctionNodes: []string{"paragraph_header", "section_header"},
		ClassNodes:    []string{"program_definition", "procedure_division"},
		CallNodes:     []string{"call_statement", "perform_statement_call_proc"},
		ImportNodes:   []string{"copy_statement"},
		NameField:     "name",
		BodyField:     "body",
		IsExported: func(name string) bool {
			return name != ""
		},
	})
}
