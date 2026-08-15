package parser

import (
	"strings"
	"unicode"

	sitter "github.com/smacker/go-tree-sitter"

	"github.com/harneet2512/groundtruth/gt-index/internal/walker"
)

// extractCOBOLParagraphCalls attaches an out-of-line PERFORM statement to the
// nearest preceding paragraph header. The pinned COBOL grammar represents
// paragraph headers and statements as siblings, so the generic function-body
// walker cannot establish this mechanically proven ownership boundary.
func extractCOBOLParagraphCalls(root *sitter.Node, sf walker.SourceFile, src []byte, result *ParseResult) {
	var walk func(*sitter.Node)
	walk = func(node *sitter.Node) {
		if node.Type() == "perform_statement_call_proc" {
			line := int(node.StartPoint().Row) + 1
			callerIndex := -1
			callerLine := 0
			for index, candidate := range result.Nodes {
				if candidate.Language == "cobol" && candidate.Label == "Function" &&
					candidate.StartLine <= line && candidate.StartLine >= callerLine {
					callerIndex = index
					callerLine = candidate.StartLine
				}
			}
			procedure := node.ChildByFieldName("procedure")
			if callerIndex >= 0 && procedure != nil {
				fields := strings.FieldsFunc(procedure.Content(src), func(r rune) bool {
					return unicode.IsSpace(r) || r == '.' || r == ','
				})
				if len(fields) > 0 && fields[0] != "" {
					result.Calls = appendUniqueCall(result.Calls, CallRef{
						CallerNodeIdx: callerIndex,
						CalleeName:    fields[0], CalleeQualified: fields[0],
						Line: line, File: sf.Path,
					})
				}
			}
		}
		for index := 0; index < int(node.ChildCount()); index++ {
			walk(node.Child(index))
		}
	}
	walk(root)
}
