// Package cobol exposes the pinned tree-sitter-cobol parser to gt-index.
package cobol

//#include "parser.h"
//TSLanguage *tree_sitter_COBOL();
import "C"
import (
	"unsafe"

	sitter "github.com/smacker/go-tree-sitter"
)

// GetLanguage returns the grammar compiled into this package.
func GetLanguage() *sitter.Language {
	return sitter.NewLanguage(unsafe.Pointer(C.tree_sitter_COBOL()))
}
