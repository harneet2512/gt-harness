package parser

import (
	"fmt"
	"strings"
	"unicode"

	"github.com/harneet2512/groundtruth/gt-index/internal/store"
	"github.com/harneet2512/groundtruth/gt-index/internal/walker"
)

type structuralToken struct {
	text string
	line int
}

func structuralTokens(source string, extra func(rune) bool) []structuralToken {
	var tokens []structuralToken
	var current strings.Builder
	line := 1
	startLine := 1
	flush := func() {
		if current.Len() == 0 {
			return
		}
		tokens = append(tokens, structuralToken{current.String(), startLine})
		current.Reset()
	}
	for _, r := range source {
		if unicode.IsLetter(r) || unicode.IsDigit(r) || r == '_' || r == '\'' || (extra != nil && extra(r)) {
			if current.Len() == 0 {
				startLine = line
			}
			current.WriteRune(r)
		} else {
			flush()
			if strings.ContainsRune("{}();,.[]", r) {
				tokens = append(tokens, structuralToken{string(r), line})
			}
		}
		if r == '\n' {
			line++
		}
	}
	flush()
	return tokens
}

func appendStructuralNode(result *ParseResult, sf walker.SourceFile, isTest bool, label, name, signature string, line int) int {
	result.Nodes = append(result.Nodes, store.Node{
		Label: label, Name: name, QualifiedName: name, FilePath: sf.Path,
		StartLine: line, EndLine: line, Signature: signature,
		IsExported: true, IsTest: isTest, Language: sf.Language,
	})
	return len(result.Nodes) - 1
}

// parseObjectiveC is deliberately a declaration adapter, not a C-family text
// search. It recognizes only Objective-C container declarations and method
// definitions whose selector is mechanically present before the method body.
// Message sends remain unindexed because proving their receiver type requires
// a full Objective-C semantic frontend.
func parseObjectiveC(sf walker.SourceFile, source string, isTest bool) *ParseResult {
	result := &ParseResult{}
	clean := stripCStyleComments(source)
	currentOwner := ""
	inImplementation := false
	seenContainers := map[string]bool{}
	seenMethods := map[string]bool{}
	for lineNo, raw := range strings.Split(clean, "\n") {
		line := strings.TrimSpace(raw)
		fields := strings.Fields(line)
		if len(fields) == 0 {
			continue
		}
		if fields[0] == "@interface" || fields[0] == "@implementation" || fields[0] == "@protocol" {
			if len(fields) < 2 {
				currentOwner = ""
				inImplementation = false
				continue
			}
			name := strings.TrimFunc(fields[1], func(r rune) bool {
				return !unicode.IsLetter(r) && !unicode.IsDigit(r) && r != '_'
			})
			currentOwner = name
			inImplementation = fields[0] == "@implementation"
			if name != "" && !seenContainers[name] {
				appendStructuralNode(result, sf, isTest, "Class", name, fields[0], lineNo+1)
				seenContainers[name] = true
			}
			continue
		}
		if fields[0] == "@end" {
			currentOwner = ""
			inImplementation = false
			continue
		}
		if !inImplementation || currentOwner == "" || (line[0] != '-' && line[0] != '+') {
			continue
		}
		closeType := strings.IndexByte(line, ')')
		if closeType < 0 || closeType+1 >= len(line) {
			continue
		}
		remainder := strings.TrimSpace(line[closeType+1:])
		selectorEnd := strings.IndexFunc(remainder, func(r rune) bool {
			return !(unicode.IsLetter(r) || unicode.IsDigit(r) || r == '_')
		})
		if selectorEnd < 0 {
			selectorEnd = len(remainder)
		}
		selector := remainder[:selectorEnd]
		if selector == "" {
			continue
		}
		if selectorEnd < len(remainder) && remainder[selectorEnd] == ':' {
			selector += ":"
		}
		qualified := currentOwner + "." + selector
		if seenMethods[qualified] {
			continue
		}
		index := appendStructuralNode(result, sf, isTest, "Function", selector, line, lineNo+1)
		result.Nodes[index].QualifiedName = qualified
		seenMethods[qualified] = true
	}
	return result
}

func parseCoq(sf walker.SourceFile, source string, isTest bool) *ParseResult {
	result := &ParseResult{}
	source = stripNestedComments(source, "(*", "*)")
	definitions := map[string]int{}
	current := -1
	declarations := map[string]bool{
		"Theorem": true, "Lemma": true, "Corollary": true, "Proposition": true,
		"Definition": true, "Fixpoint": true, "CoFixpoint": true,
		"Inductive": true, "CoInductive": true, "Record": true,
		"Class": true, "Instance": true, "Module": true,
	}
	for lineNo, raw := range strings.Split(source, "\n") {
		line := strings.TrimSpace(raw)
		fields := strings.Fields(line)
		if len(fields) == 0 {
			continue
		}
		if fields[0] == "Require" || fields[0] == "From" {
			importAt := -1
			for index, field := range fields {
				if field == "Import" || field == "Export" {
					importAt = index + 1
					break
				}
			}
			for index := importAt; index >= 0 && index < len(fields); index++ {
				module := strings.TrimRight(fields[index], ".")
				if module != "" {
					result.Imports = append(result.Imports, ImportRef{ImportedName: module, ModulePath: module, File: sf.Path, Line: lineNo + 1})
				}
			}
		}
		if declarations[fields[0]] && len(fields) > 1 {
			name := strings.TrimRight(fields[1], ":.({")
			if isIdentifier(name) {
				current = appendStructuralNode(result, sf, isTest, "Function", name, fields[0], lineNo+1)
				definitions[name] = current
			}
		}
		if current < 0 {
			continue
		}
		for _, token := range structuralTokens(line, func(r rune) bool { return r == '.' }) {
			target := strings.Trim(token.text, ".")
			if dot := strings.LastIndex(target, "."); dot >= 0 {
				target = target[dot+1:]
			}
			if targetID, found := definitions[target]; found && targetID != current {
				result.Calls = appendUniqueCall(result.Calls, CallRef{CallerNodeIdx: current, CalleeName: target, CalleeQualified: target, Line: lineNo + 1, File: sf.Path})
			}
		}
		if fields[0] == "Qed." || fields[0] == "Defined." || fields[0] == "Admitted." {
			current = -1
		}
	}
	return result
}

func parseStan(sf walker.SourceFile, source string, isTest bool) *ParseResult {
	result := &ParseResult{}
	tokens := structuralTokens(stripCStyleComments(source), nil)
	definitions := map[string]int{}
	typeNames := map[string]bool{"void": true, "int": true, "real": true, "vector": true, "row_vector": true, "matrix": true, "array": true}
	functionsDepth := -1
	depth := 0
	blockNames := map[string]bool{
		"data": true, "parameters": true, "model": true,
	}
	type functionBody struct{ owner, start, end int }
	var bodies []functionBody
	for index := 0; index < len(tokens); index++ {
		token := tokens[index].text
		if blockNames[token] && index+1 < len(tokens) && tokens[index+1].text == "{" && (index == 0 || tokens[index-1].text != "transformed") {
			appendStructuralNode(result, sf, isTest, "Class", token, "stan_block", tokens[index].line)
		}
		if token == "transformed" && index+2 < len(tokens) && (tokens[index+1].text == "data" || tokens[index+1].text == "parameters") && tokens[index+2].text == "{" {
			name := token + "_" + tokens[index+1].text
			appendStructuralNode(result, sf, isTest, "Class", name, "stan_block", tokens[index].line)
		}
		if token == "generated" && index+2 < len(tokens) && tokens[index+1].text == "quantities" && tokens[index+2].text == "{" {
			appendStructuralNode(result, sf, isTest, "Class", "generated_quantities", "stan_block", tokens[index].line)
		}
		if token == "functions" && index+1 < len(tokens) && tokens[index+1].text == "{" {
			functionsDepth = depth + 1
		}
		if token == "{" {
			depth++
		} else if token == "}" {
			depth--
			if functionsDepth > 0 && depth < functionsDepth {
				functionsDepth = -1
			}
		}
		if functionsDepth < 0 || depth != functionsDepth || index+2 >= len(tokens) || !typeNames[token] || !isIdentifier(tokens[index+1].text) || tokens[index+2].text != "(" {
			continue
		}
		closeParen := matchingToken(tokens, index+2, "(", ")")
		if closeParen < 0 || closeParen+1 >= len(tokens) || tokens[closeParen+1].text != "{" {
			continue
		}
		name := tokens[index+1].text
		owner := appendStructuralNode(result, sf, isTest, "Function", name, token, tokens[index].line)
		definitions[name] = owner
		closeBody := matchingToken(tokens, closeParen+1, "{", "}")
		if closeBody > closeParen+1 {
			bodies = append(bodies, functionBody{owner, closeParen + 2, closeBody})
		}
	}
	for _, body := range bodies {
		for index := body.start; index+1 < body.end; index++ {
			name := tokens[index].text
			if targetOwner, found := definitions[name]; found && tokens[index+1].text == "(" && targetOwner != body.owner {
				result.Calls = appendUniqueCall(result.Calls, CallRef{CallerNodeIdx: body.owner, CalleeName: name, CalleeQualified: name, Line: tokens[index].line, File: sf.Path})
			}
		}
	}
	return result
}

func parseSPARQL(sf walker.SourceFile, source string, isTest bool) *ParseResult {
	result := &ParseResult{}
	for lineNo, raw := range strings.Split(source, "\n") {
		line := strings.TrimSpace(stripHashComment(raw))
		fields := strings.Fields(line)
		if len(fields) == 0 {
			continue
		}
		keyword := strings.ToUpper(fields[0])
		if (keyword == "PREFIX" || keyword == "BASE") && len(fields) > 1 {
			name := strings.TrimRight(fields[1], ":")
			result.Imports = append(result.Imports, ImportRef{ImportedName: name, ModulePath: strings.Trim(fields[len(fields)-1], "<>"), File: sf.Path, Line: lineNo + 1})
		}
	}
	return result
}

func parseTurtle(sf walker.SourceFile, source string, isTest bool) *ParseResult {
	result := &ParseResult{}
	seen := map[string]bool{}
	for lineNo, raw := range strings.Split(source, "\n") {
		line := strings.TrimSpace(stripHashComment(raw))
		fields := strings.Fields(line)
		if len(fields) == 0 {
			continue
		}
		keyword := strings.ToLower(fields[0])
		if (keyword == "@prefix" || keyword == "prefix" || keyword == "@base" || keyword == "base") && len(fields) > 2 {
			prefix := strings.TrimRight(fields[1], ":")
			result.Imports = append(result.Imports, ImportRef{ImportedName: prefix, ModulePath: strings.Trim(fields[2], "<>."), File: sf.Path, Line: lineNo + 1})
			continue
		}
		subject := strings.Trim(fields[0], "<>;,.[]")
		if subject != "" && (strings.Contains(subject, ":") || strings.HasPrefix(fields[0], "<")) && !seen[subject] {
			seen[subject] = true
			appendStructuralNode(result, sf, isTest, "Class", subject, "rdf_subject", lineNo+1)
		}
	}
	return result
}

func parseLaTeX(sf walker.SourceFile, source string, isTest bool) *ParseResult {
	result := &ParseResult{}
	definitions := map[string]int{}
	current := -1
	for index := 0; index < len(source); {
		if source[index] != '\\' {
			index++
			continue
		}
		command, next := latexCommand(source, index)
		line := 1 + strings.Count(source[:index], "\n")
		index = next
		switch command {
		case "newcommand", "renewcommand", "providecommand", "DeclareRobustCommand":
			name, after := latexBracedCommand(source, index)
			if name != "" {
				current = appendStructuralNode(result, sf, isTest, "Function", name, command, line)
				definitions[name] = current
				index = after
			}
		case "newenvironment", "renewenvironment":
			name, after := latexBracedText(source, index)
			if name != "" {
				current = appendStructuralNode(result, sf, isTest, "Class", name, command, line)
				definitions[name] = current
				index = after
			}
		case "input", "include", "usepackage", "documentclass":
			module, after := latexBracedText(source, index)
			if module != "" {
				result.Imports = append(result.Imports, ImportRef{ImportedName: module, ModulePath: module, File: sf.Path, Line: line})
				index = after
			}
		default:
			if targetOwner, found := definitions[command]; current >= 0 && found && targetOwner != current {
				result.Calls = appendUniqueCall(result.Calls, CallRef{CallerNodeIdx: current, CalleeName: command, CalleeQualified: command, Line: line, File: sf.Path})
			}
		}
	}
	return result
}

func parseVim(sf walker.SourceFile, source string, isTest bool) *ParseResult {
	result := &ParseResult{}
	definitions := map[string]int{}
	current := -1
	for lineNo, raw := range strings.Split(source, "\n") {
		line := strings.TrimSpace(raw)
		if line == "" || strings.HasPrefix(line, "\"") {
			continue
		}
		fields := strings.Fields(line)
		if len(fields) == 0 {
			continue
		}
		command := strings.TrimSuffix(strings.ToLower(fields[0]), "!")
		if command == "function" && len(fields) > 1 {
			name := strings.TrimSuffix(fields[1], "()")
			if open := strings.IndexByte(name, '('); open >= 0 {
				name = name[:open]
			}
			if name != "" {
				current = appendStructuralNode(result, sf, isTest, "Function", name, fields[0], lineNo+1)
				definitions[name] = current
			}
		} else if command == "endfunction" {
			current = -1
		} else if command == "source" || command == "runtime" {
			if len(fields) > 1 {
				result.Imports = append(result.Imports, ImportRef{ImportedName: fields[1], ModulePath: fields[1], File: sf.Path, Line: lineNo + 1})
			}
		} else if command == "call" && len(fields) > 1 && strings.HasPrefix(strings.ToLower(fields[1]), "setreg(") {
			argument := fields[1]
			quote := strings.IndexAny(argument, "'\"")
			if quote >= 0 && quote+1 < len(argument) {
				register := argument[quote+1 : quote+2]
				if isIdentifier(register) {
					appendStructuralNode(result, sf, isTest, "Type", "register_"+register, "setreg", lineNo+1)
				}
			}
		} else if command == "call" && len(fields) > 1 && current >= 0 {
			name := fields[1]
			if open := strings.IndexByte(name, '('); open >= 0 {
				name = name[:open]
			}
			if targetOwner, found := definitions[name]; found && targetOwner != current {
				result.Calls = appendUniqueCall(result.Calls, CallRef{CallerNodeIdx: current, CalleeName: name, CalleeQualified: name, Line: lineNo + 1, File: sf.Path})
			}
		}
	}
	return result
}

func parseNginx(sf walker.SourceFile, source string, isTest bool) *ParseResult {
	result := &ParseResult{}
	for lineNo, raw := range strings.Split(source, "\n") {
		line := strings.TrimSpace(stripHashComment(raw))
		fields := strings.Fields(line)
		if len(fields) == 0 {
			continue
		}
		keyword := fields[0]
		if keyword == "include" && len(fields) > 1 {
			module := strings.TrimRight(fields[1], ";")
			result.Imports = append(result.Imports, ImportRef{ImportedName: module, ModulePath: module, File: sf.Path, Line: lineNo + 1})
		}
		if !strings.Contains(line, "{") {
			continue
		}
		name := ""
		switch keyword {
		case "upstream", "map", "location":
			if len(fields) > 1 {
				name = strings.TrimRight(fields[1], "{")
			}
		case "server", "http", "events", "stream", "mail":
			name = fmt.Sprintf("%s@%d", keyword, lineNo+1)
		}
		if name != "" {
			appendStructuralNode(result, sf, isTest, "Class", name, keyword, lineNo+1)
		}
	}
	return result
}

func parseGCode(sf walker.SourceFile, source string, isTest bool) *ParseResult {
	result := &ParseResult{}
	definitions := map[string]int{}
	current := -1
	type pendingCall struct {
		owner  int
		target string
		line   int
	}
	var pending []pendingCall
	for lineNo, raw := range strings.Split(source, "\n") {
		line := strings.TrimSpace(stripGCodeComment(raw))
		fields := strings.Fields(line)
		if len(fields) == 0 {
			continue
		}
		first := strings.ToUpper(fields[0])
		if len(first) > 1 && first[0] == 'O' && allDigits(first[1:]) {
			current = appendStructuralNode(result, sf, isTest, "Function", first, "subprogram", lineNo+1)
			definitions[first] = current
			continue
		}
		if current < 0 {
			continue
		}
		for index, field := range fields {
			upper := strings.ToUpper(field)
			if (upper == "M98" || upper == "M97") && index+1 < len(fields) {
				target := strings.ToUpper(fields[index+1])
				if strings.HasPrefix(target, "P") && allDigits(target[1:]) {
					pending = append(pending, pendingCall{current, "O" + target[1:], lineNo + 1})
				}
			}
		}
	}
	for _, call := range pending {
		if _, found := definitions[call.target]; found {
			result.Calls = appendUniqueCall(result.Calls, CallRef{CallerNodeIdx: call.owner, CalleeName: call.target, CalleeQualified: call.target, Line: call.line, File: sf.Path})
		}
	}
	return result
}

func parseMake(sf walker.SourceFile, source string, isTest bool) *ParseResult {
	result := &ParseResult{}
	definitions := map[string]int{}
	type dependencyRow struct {
		owner int
		line  int
		names []string
	}
	var dependencies []dependencyRow
	for lineNo, raw := range strings.Split(source, "\n") {
		if strings.HasPrefix(raw, "\t") {
			continue
		}
		line := strings.TrimSpace(stripHashComment(raw))
		colon := strings.IndexByte(line, ':')
		if colon <= 0 || strings.Contains(line[:colon], "=") {
			continue
		}
		left := strings.Fields(line[:colon])
		if len(left) != 1 || strings.ContainsAny(left[0], "%$(){}") {
			continue
		}
		name := left[0]
		owner := appendStructuralNode(result, sf, isTest, "Function", name, "make_target", lineNo+1)
		definitions[name] = owner
		dependencies = append(dependencies, dependencyRow{owner, lineNo + 1, strings.Fields(line[colon+1:])})
	}
	for _, row := range dependencies {
		for _, name := range row.names {
			if targetOwner, found := definitions[name]; found && targetOwner != row.owner {
				result.Calls = appendUniqueCall(result.Calls, CallRef{CallerNodeIdx: row.owner, CalleeName: name, CalleeQualified: name, Line: row.line, File: sf.Path})
			}
		}
	}
	return result
}

func parseDockerfile(sf walker.SourceFile, source string, isTest bool) *ParseResult {
	result := &ParseResult{}
	stage := 0
	for lineNo, raw := range strings.Split(source, "\n") {
		line := strings.TrimSpace(stripHashComment(raw))
		fields := strings.Fields(line)
		if len(fields) < 2 || strings.ToUpper(fields[0]) != "FROM" {
			continue
		}
		image := fields[1]
		name := fmt.Sprintf("stage%d", stage)
		stage++
		for index := 2; index+1 < len(fields); index++ {
			if strings.ToUpper(fields[index]) == "AS" {
				name = fields[index+1]
				break
			}
		}
		appendStructuralNode(result, sf, isTest, "Class", name, "FROM "+image, lineNo+1)
		result.Imports = append(result.Imports, ImportRef{ImportedName: image, ModulePath: image, File: sf.Path, Line: lineNo + 1})
	}
	return result
}

func parseCMake(sf walker.SourceFile, source string, isTest bool) *ParseResult {
	result := &ParseResult{}
	definitions := map[string]int{}
	current := -1
	for lineNo, raw := range strings.Split(source, "\n") {
		line := strings.TrimSpace(stripHashComment(raw))
		command, argument := firstCallArgument(line)
		lower := strings.ToLower(command)
		if command == "" {
			continue
		}
		switch lower {
		case "function", "macro":
			if isIdentifier(argument) {
				current = appendStructuralNode(result, sf, isTest, "Function", argument, lower, lineNo+1)
				definitions[strings.ToLower(argument)] = current
			}
		case "endfunction", "endmacro":
			current = -1
		case "add_executable", "add_library", "project":
			if argument != "" {
				appendStructuralNode(result, sf, isTest, "Class", argument, lower, lineNo+1)
			}
		case "include", "add_subdirectory":
			if argument != "" {
				result.Imports = append(result.Imports, ImportRef{ImportedName: argument, ModulePath: argument, File: sf.Path, Line: lineNo + 1})
			}
		default:
			if targetOwner, found := definitions[lower]; current >= 0 && found && targetOwner != current {
				result.Calls = appendUniqueCall(result.Calls, CallRef{CallerNodeIdx: current, CalleeName: argumentOr(command, command), CalleeQualified: command, Line: lineNo + 1, File: sf.Path})
			}
		}
	}
	return result
}

func parseMeson(sf walker.SourceFile, source string, isTest bool) *ParseResult {
	result := &ParseResult{}
	for lineNo, raw := range strings.Split(source, "\n") {
		line := strings.TrimSpace(stripHashComment(raw))
		command, argument := firstCallArgument(line)
		switch strings.ToLower(command) {
		case "project", "executable", "library", "shared_library", "static_library", "custom_target", "run_target":
			if argument != "" {
				label := "Class"
				if command == "custom_target" || command == "run_target" {
					label = "Function"
				}
				appendStructuralNode(result, sf, isTest, label, argument, command, lineNo+1)
			}
		case "subdir", "include_directories":
			if argument != "" {
				result.Imports = append(result.Imports, ImportRef{ImportedName: argument, ModulePath: argument, File: sf.Path, Line: lineNo + 1})
			}
		}
	}
	return result
}

func parseAutotools(sf walker.SourceFile, source string, isTest bool) *ParseResult {
	result := &ParseResult{}
	for lineNo, raw := range strings.Split(source, "\n") {
		line := strings.TrimSpace(stripHashComment(raw))
		command, argument := firstCallArgument(line)
		upper := strings.ToUpper(command)
		switch upper {
		case "AC_INIT", "AM_INIT_AUTOMAKE", "AC_DEFUN":
			if argument != "" {
				appendStructuralNode(result, sf, isTest, "Function", argument, upper, lineNo+1)
			}
		case "AC_CONFIG_FILES", "AC_CONFIG_HEADERS", "AC_CONFIG_SUBDIRS":
			if argument != "" {
				result.Imports = append(result.Imports, ImportRef{ImportedName: argument, ModulePath: argument, File: sf.Path, Line: lineNo + 1})
			}
		}
	}
	return result
}

func firstCallArgument(line string) (string, string) {
	open := strings.IndexByte(line, '(')
	if open <= 0 {
		return "", ""
	}
	command := strings.TrimSpace(line[:open])
	close := strings.LastIndexByte(line, ')')
	if close <= open {
		return command, ""
	}
	argument := strings.TrimSpace(line[open+1 : close])
	if comma := strings.IndexByte(argument, ','); comma >= 0 {
		argument = argument[:comma]
	}
	argument = strings.Trim(strings.TrimSpace(argument), "[]'\"")
	return command, argument
}

func argumentOr(value, fallback string) string {
	if value != "" {
		return value
	}
	return fallback
}

func matchingToken(tokens []structuralToken, start int, open, close string) int {
	depth := 0
	for index := start; index < len(tokens); index++ {
		if tokens[index].text == open {
			depth++
		} else if tokens[index].text == close {
			depth--
			if depth == 0 {
				return index
			}
		}
	}
	return -1
}

func appendUniqueCall(calls []CallRef, candidate CallRef) []CallRef {
	for _, call := range calls {
		if call.CallerNodeIdx == candidate.CallerNodeIdx && call.CalleeName == candidate.CalleeName && call.Line == candidate.Line {
			return calls
		}
	}
	return append(calls, candidate)
}

func isIdentifier(value string) bool {
	if value == "" {
		return false
	}
	for index, r := range value {
		if !(unicode.IsLetter(r) || r == '_' || r == '\'' || index > 0 && unicode.IsDigit(r)) {
			return false
		}
	}
	return true
}

func allDigits(value string) bool {
	if value == "" {
		return false
	}
	for _, r := range value {
		if !unicode.IsDigit(r) {
			return false
		}
	}
	return true
}

func stripHashComment(line string) string {
	if index := strings.IndexByte(line, '#'); index >= 0 {
		return line[:index]
	}
	return line
}

func stripGCodeComment(line string) string {
	if index := strings.IndexByte(line, ';'); index >= 0 {
		line = line[:index]
	}
	for {
		start := strings.IndexByte(line, '(')
		if start < 0 {
			return line
		}
		end := strings.IndexByte(line[start+1:], ')')
		if end < 0 {
			return line[:start]
		}
		line = line[:start] + line[start+1+end+1:]
	}
}

func stripNestedComments(source, open, close string) string {
	var out strings.Builder
	depth := 0
	for index := 0; index < len(source); {
		if strings.HasPrefix(source[index:], open) {
			depth++
			out.WriteString(strings.Repeat(" ", len(open)))
			index += len(open)
			continue
		}
		if depth > 0 && strings.HasPrefix(source[index:], close) {
			depth--
			out.WriteString(strings.Repeat(" ", len(close)))
			index += len(close)
			continue
		}
		if depth > 0 && source[index] != '\n' {
			out.WriteByte(' ')
		} else {
			out.WriteByte(source[index])
		}
		index++
	}
	return out.String()
}

func stripCStyleComments(source string) string {
	var out strings.Builder
	inBlock := false
	for _, raw := range strings.Split(source, "\n") {
		line := raw
		if inBlock {
			if end := strings.Index(line, "*/"); end >= 0 {
				line = line[end+2:]
				inBlock = false
			} else {
				out.WriteByte('\n')
				continue
			}
		}
		for {
			start := strings.Index(line, "/*")
			if start < 0 {
				break
			}
			end := strings.Index(line[start+2:], "*/")
			if end < 0 {
				line = line[:start]
				inBlock = true
				break
			}
			line = line[:start] + line[start+2+end+2:]
		}
		if slash := strings.Index(line, "//"); slash >= 0 {
			line = line[:slash]
		}
		out.WriteString(line)
		out.WriteByte('\n')
	}
	return out.String()
}

func latexCommand(source string, start int) (string, int) {
	index := start + 1
	for index < len(source) && (unicode.IsLetter(rune(source[index])) || source[index] == '@') {
		index++
	}
	return source[start+1 : index], index
}

func latexBracedText(source string, start int) (string, int) {
	for start < len(source) && unicode.IsSpace(rune(source[start])) {
		start++
	}
	if start >= len(source) || source[start] != '{' {
		return "", start
	}
	end := strings.IndexByte(source[start+1:], '}')
	if end < 0 {
		return "", start
	}
	return strings.TrimSpace(source[start+1 : start+1+end]), start + 1 + end + 1
}

func latexBracedCommand(source string, start int) (string, int) {
	text, end := latexBracedText(source, start)
	return strings.TrimPrefix(text, "\\"), end
}
