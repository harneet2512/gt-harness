// Package scheme exposes the pinned tree-sitter-scheme parser to gt-index.
package scheme

//#include "parser.h"
//TSLanguage *tree_sitter_scheme();
import "C"
import (
	"unsafe"

	sitter "github.com/smacker/go-tree-sitter"
)

// GetLanguage returns the grammar compiled into this package.
func GetLanguage() *sitter.Language {
	return sitter.NewLanguage(unsafe.Pointer(C.tree_sitter_scheme()))
}
