package parser

import (
	"strings"
	"unicode"

	"github.com/harneet2512/groundtruth/gt-index/internal/store"
	"github.com/harneet2512/groundtruth/gt-index/internal/walker"
)

// parseStructuredSource handles small, deliberately bounded adapters for
// source languages without a certified Tree-sitter binding. These are not
// regex fallbacks:
// each adapter has a token/statement grammar, explicit node kinds, and only
// emits facts that its grammar proves.  Unknown syntax is retained as source
// but produces no speculative graph edge.
func parseStructuredSource(sf walker.SourceFile, src []byte, isTest bool) (*ParseResult, error) {
	switch sf.Language {
	case "red":
		return ensureStructuredFileNode(parseRedcode(sf, string(src), isTest), sf, src, isTest), nil
	case "povray":
		return ensureStructuredFileNode(parsePOVRay(sf, string(src), isTest), sf, src, isTest), nil
	case "coq":
		return ensureStructuredFileNode(parseCoq(sf, string(src), isTest), sf, src, isTest), nil
	case "stan":
		return ensureStructuredFileNode(parseStan(sf, string(src), isTest), sf, src, isTest), nil
	case "sparql":
		return ensureStructuredFileNode(parseSPARQL(sf, string(src), isTest), sf, src, isTest), nil
	case "turtle":
		return ensureStructuredFileNode(parseTurtle(sf, string(src), isTest), sf, src, isTest), nil
	case "latex":
		return ensureStructuredFileNode(parseLaTeX(sf, string(src), isTest), sf, src, isTest), nil
	case "vim":
		return ensureStructuredFileNode(parseVim(sf, string(src), isTest), sf, src, isTest), nil
	case "nginx":
		return ensureStructuredFileNode(parseNginx(sf, string(src), isTest), sf, src, isTest), nil
	case "gcode":
		return ensureStructuredFileNode(parseGCode(sf, string(src), isTest), sf, src, isTest), nil
	case "make":
		return ensureStructuredFileNode(parseMake(sf, string(src), isTest), sf, src, isTest), nil
	case "dockerfile":
		return ensureStructuredFileNode(parseDockerfile(sf, string(src), isTest), sf, src, isTest), nil
	case "cmake":
		return ensureStructuredFileNode(parseCMake(sf, string(src), isTest), sf, src, isTest), nil
	case "meson":
		return ensureStructuredFileNode(parseMeson(sf, string(src), isTest), sf, src, isTest), nil
	case "autotools":
		return ensureStructuredFileNode(parseAutotools(sf, string(src), isTest), sf, src, isTest), nil
	case "objective_c":
		return ensureStructuredFileNode(parseObjectiveC(sf, string(src), isTest), sf, src, isTest), nil
	default:
		return nil, nil
	}
}

func ensureStructuredFileNode(result *ParseResult, sf walker.SourceFile, src []byte, isTest bool) *ParseResult {
	if result == nil {
		result = &ParseResult{}
	}
	if len(result.Nodes) == 0 && strings.TrimSpace(string(src)) != "" {
		result.Nodes = append(result.Nodes, store.Node{
			Label: "File", Name: sf.Path, QualifiedName: sf.Path, FilePath: sf.Path,
			StartLine: 1, EndLine: len(strings.Split(string(src), "\n")),
			IsExported: true, IsTest: isTest, Language: sf.Language,
		})
	}
	return result
}

func parseRedcode(sf walker.SourceFile, source string, isTest bool) *ParseResult {
	type statement struct {
		line  int
		label string
		owner string
		op    string
		args  []string
	}
	knownOps := map[string]bool{
		"DAT": true, "MOV": true, "ADD": true, "SUB": true, "MUL": true,
		"DIV": true, "MOD": true, "JMP": true, "JMZ": true, "JMN": true,
		"DJN": true, "SPL": true, "SLT": true, "CMP": true, "SEQ": true,
		"SNE": true, "NOP": true, "LDP": true, "STP": true,
	}
	var statements []statement
	currentLabel := ""
	for lineNo, raw := range strings.Split(source, "\n") {
		line := stripRedComment(raw)
		if strings.TrimSpace(line) == "" {
			continue
		}
		fields := strings.FieldsFunc(line, func(r rune) bool { return unicode.IsSpace(r) || r == ',' })
		if len(fields) == 0 {
			continue
		}
		upper := strings.ToUpper(fields[0])
		if upper == "END" || upper == "ORG" || upper == "EQU" || upper == "FOR" || upper == "ROF" {
			continue
		}
		label := ""
		opIndex := 0
		if !knownOps[upper] {
			label = fields[0]
			opIndex = 1
		}
		if opIndex >= len(fields) || !knownOps[strings.ToUpper(fields[opIndex])] {
			continue
		}
		if label != "" {
			currentLabel = label
		}
		op := strings.ToUpper(fields[opIndex])
		args := append([]string(nil), fields[opIndex+1:]...)
		statements = append(statements, statement{line: lineNo + 1, label: label, owner: currentLabel, op: op, args: args})
	}
	result := &ParseResult{}
	nodeIDs := map[string]int{}
	for _, stmt := range statements {
		if stmt.label == "" {
			continue
		}
		nodeIDs[stmt.label] = len(result.Nodes)
		result.Nodes = append(result.Nodes, store.Node{
			Label: "Function", Name: stmt.label, QualifiedName: stmt.label,
			FilePath: sf.Path, StartLine: stmt.line, EndLine: stmt.line,
			Signature: stmt.op, IsExported: true, IsTest: isTest, Language: sf.Language,
		})
	}
	for _, stmt := range statements {
		if stmt.owner == "" || len(stmt.args) == 0 {
			continue
		}
		caller, callerFound := nodeIDs[stmt.owner]
		if !callerFound {
			continue
		}
		for _, arg := range stmt.args {
			target := strings.Trim(arg, "#@<>{}()*+$")
			if target == "" {
				continue
			}
			if _, targetFound := nodeIDs[target]; targetFound && isRedControlFlow(stmt.op) {
				result.Calls = append(result.Calls, CallRef{
					CallerNodeIdx: caller, CalleeName: target, CalleeQualified: target,
					Line: stmt.line, File: sf.Path,
				})
			}
		}
	}
	return result
}

func isRedControlFlow(op string) bool {
	switch op {
	case "JMP", "JMZ", "JMN", "DJN", "SPL":
		return true
	default:
		return false
	}
}

func stripRedComment(line string) string {
	if index := strings.IndexByte(line, ';'); index >= 0 {
		return line[:index]
	}
	return line
}

func parsePOVRay(sf walker.SourceFile, source string, isTest bool) *ParseResult {
	result := &ParseResult{}
	macros := map[string]int{}
	clean := stripPOVComments(source)
	for lineNo, raw := range strings.Split(clean, "\n") {
		fields := strings.Fields(raw)
		if len(fields) == 0 {
			continue
		}
		if fields[0] == "#include" && len(fields) > 1 {
			module := strings.Trim(fields[1], "\"<>")
			if module != "" {
				result.Imports = append(result.Imports, ImportRef{ImportedName: module, ModulePath: module, File: sf.Path, Line: lineNo + 1})
			}
			continue
		}
		if fields[0] != "#macro" && fields[0] != "#declare" {
			continue
		}
		if len(fields) < 2 {
			continue
		}
		name := strings.TrimFunc(fields[1], func(r rune) bool { return !unicode.IsLetter(r) && !unicode.IsDigit(r) && r != '_' })
		if name == "" {
			continue
		}
		label := "Function"
		if fields[0] == "#declare" {
			label = "Class"
		}
		macros[name] = len(result.Nodes)
		result.Nodes = append(result.Nodes, store.Node{
			Label: label, Name: name, QualifiedName: name, FilePath: sf.Path,
			StartLine: lineNo + 1, EndLine: lineNo + 1, Signature: fields[0],
			IsExported: true, IsTest: isTest, Language: sf.Language,
		})
	}
	currentMacro := -1
	for lineNo, raw := range strings.Split(clean, "\n") {
		trimmed := strings.TrimSpace(raw)
		fields := strings.Fields(trimmed)
		if len(fields) > 1 && fields[0] == "#macro" {
			name := strings.TrimFunc(fields[1], func(r rune) bool {
				return !unicode.IsLetter(r) && !unicode.IsDigit(r) && r != '_'
			})
			if owner, found := macros[name]; found {
				currentMacro = owner
			}
			continue
		}
		if strings.HasPrefix(trimmed, "#end") {
			currentMacro = -1
			continue
		}
		if currentMacro < 0 || strings.HasPrefix(trimmed, "#declare") {
			continue
		}
		tokens := povIdentifiers(raw)
		for i := 0; i+1 < len(tokens); i++ {
			if tokens[i+1] != "(" {
				continue
			}
			target, found := macros[tokens[i]]
			if !found || target == currentMacro {
				continue
			}
			result.Calls = append(result.Calls, CallRef{CallerNodeIdx: currentMacro, CalleeName: tokens[i], CalleeQualified: tokens[i], Line: lineNo + 1, File: sf.Path})
		}
	}
	if len(result.Nodes) == 0 && strings.TrimSpace(clean) != "" {
		result.Nodes = append(result.Nodes, store.Node{Label: "File", Name: sf.Path, QualifiedName: sf.Path, FilePath: sf.Path, StartLine: 1, EndLine: len(strings.Split(clean, "\n")), IsTest: isTest, Language: sf.Language})
	}
	return result
}

func stripPOVComments(source string) string {
	var out strings.Builder
	inBlock := false
	for _, line := range strings.Split(source, "\n") {
		text := line
		if inBlock {
			if end := strings.Index(text, "*/"); end >= 0 {
				text, inBlock = text[end+2:], false
			} else {
				out.WriteByte('\n')
				continue
			}
		}
		for {
			start := strings.Index(text, "/*")
			if start < 0 {
				break
			}
			end := strings.Index(text[start+2:], "*/")
			if end < 0 {
				text, inBlock = text[:start], true
				break
			}
			text = text[:start] + text[start+2+end+2:]
		}
		if slash := strings.Index(text, "//"); slash >= 0 {
			text = text[:slash]
		}
		out.WriteString(text)
		out.WriteByte('\n')
	}
	return out.String()
}

func povIdentifiers(line string) []string {
	var tokens []string
	var current strings.Builder
	flush := func() {
		if current.Len() > 0 {
			tokens = append(tokens, current.String())
			current.Reset()
		}
	}
	for _, r := range line {
		switch {
		case unicode.IsLetter(r) || unicode.IsDigit(r) || r == '_':
			current.WriteRune(r)
		default:
			flush()
			if r == '(' {
				tokens = append(tokens, "(")
			}
		}
	}
	flush()
	return tokens
}
