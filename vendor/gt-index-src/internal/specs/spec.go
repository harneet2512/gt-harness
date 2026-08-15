// Package specs defines language-specific tree-sitter node type mappings.
// The indexer core NEVER checks language names — all language-specific
// behavior lives here.
package specs

import (
	"bytes"
	"path/filepath"
	"strings"

	sitter "github.com/smacker/go-tree-sitter"
)

// Spec maps tree-sitter node types to GT's abstract schema for one language.
type Spec struct {
	Name       string
	Extensions []string
	Basenames  []string

	// Tree-sitter node type names
	FunctionNodes []string // e.g. "function_definition" for Python
	ClassNodes    []string // e.g. "class_definition" for Python
	CallNodes     []string // e.g. "call" for Python
	ImportNodes   []string // e.g. "import_statement" for Python

	// Naming conventions
	TestFuncPattern   string   // regex for test function names
	AssertionPatterns []string // regex for assertion statements

	// Tree-sitter field names (vary by grammar)
	NameField       string // field containing the identifier name
	ReturnTypeField string // field containing return type annotation
	BodyField       string // field containing function body
	ParamsField     string // field containing parameters

	// Export detection
	IsExported func(name string) bool // language-specific export check

	// The tree-sitter Language object
	Language *sitter.Language
}

// Registry maps file extensions to every possible language spec. An extension
// is not itself a language identity: Coq/Rocq and Verilog both conventionally
// use .v, so callers must use ResolveSource before parsing.
var Registry = map[string][]*Spec{}
var basenameRegistry = map[string][]*Spec{}
var nameRegistry = map[string]*Spec{}

// Register adds a spec to the registry for all its extensions.
func Register(s *Spec) {
	nameRegistry[s.Name] = s
	for _, ext := range s.Extensions {
		key := strings.ToLower(ext)
		Registry[key] = append(Registry[key], s)
	}
	for _, basename := range s.Basenames {
		key := strings.ToLower(basename)
		basenameRegistry[key] = append(basenameRegistry[key], s)
	}
}

// ForExtension returns a spec only when the extension has one unambiguous
// language. Shared extensions deliberately return nil.
func ForExtension(ext string) *Spec {
	candidates := Registry[strings.ToLower(ext)]
	if len(candidates) != 1 {
		return nil
	}
	return candidates[0]
}

// HasCandidateExtension reports whether the path can be authored source. It
// does not claim that a shared or broad extension has been resolved.
func HasCandidateExtension(ext string) bool {
	return len(Registry[strings.ToLower(ext)]) > 0
}

// HasCandidatePath includes exact build-system basenames and extensionless
// scripts whose shebang must be inspected before dispatch.
func HasCandidatePath(path string) bool {
	return HasCandidateExtension(filepath.Ext(path)) || len(basenameRegistry[strings.ToLower(filepath.Base(path))]) > 0 || filepath.Ext(path) == ""
}

func sourceCandidates(path string) []*Spec {
	candidates := append([]*Spec(nil), Registry[strings.ToLower(filepath.Ext(path))]...)
	for _, candidate := range basenameRegistry[strings.ToLower(filepath.Base(path))] {
		found := false
		for _, existing := range candidates {
			if existing == candidate {
				found = true
				break
			}
		}
		if !found {
			candidates = append(candidates, candidate)
		}
	}
	return candidates
}

// ResolutionNeedsContent prevents the walker from reading source twice when a
// unique extension or basename already proves the parser identity.
func ResolutionNeedsContent(path string) bool {
	candidates := sourceCandidates(path)
	if len(candidates) == 0 {
		return filepath.Ext(path) == ""
	}
	return len(candidates) > 1 || candidates[0].Name == "nginx"
}

// ResolveSource selects a parser using path plus a bounded content prefix.
// Unknown and conflicting signatures abstain; they never fall back to a
// destructive guess.
func ResolveSource(path string, source []byte) (*Spec, string) {
	candidates := sourceCandidates(path)
	if len(candidates) == 0 {
		if spec := resolveShebang(source); spec != nil {
			return spec, "shebang_interpreter"
		}
		return nil, "no_source_identity"
	}
	prefix := source
	if len(prefix) > 65536 {
		prefix = prefix[:65536]
	}
	if len(candidates) == 1 {
		if candidates[0].Name == "nginx" {
			if !hasNginxSignature(prefix) {
				return nil, "nginx_signature_absent"
			}
			return candidates[0], "content_signature_nginx"
		}
		return candidates[0], "unique_extension"
	}
	names := map[string]*Spec{}
	for _, candidate := range candidates {
		names[candidate.Name] = candidate
	}
	if names["coq"] != nil && names["verilog"] != nil && len(names) == 2 {
		coq := hasCoqSignature(stripNestedComments(prefix, []byte("(*"), []byte("*)")))
		verilog := hasVerilogSignature(stripCComments(prefix))
		if coq != verilog {
			if coq {
				return names["coq"], "content_signature_coq"
			}
			return names["verilog"], "content_signature_verilog"
		}
		if coq {
			return nil, "conflicting_content_signatures"
		}
		return nil, "ambiguous_extension"
	}
	return nil, "ambiguous_extension"
}

func stripNestedComments(source, opening, closing []byte) []byte {
	result := make([]byte, len(source))
	depth := 0
	for index := 0; index < len(source); {
		if bytes.HasPrefix(source[index:], opening) {
			depth++
			for offset := range opening {
				result[index+offset] = ' '
			}
			index += len(opening)
			continue
		}
		if depth > 0 && bytes.HasPrefix(source[index:], closing) {
			depth--
			for offset := range closing {
				result[index+offset] = ' '
			}
			index += len(closing)
			continue
		}
		if depth == 0 || source[index] == '\n' {
			result[index] = source[index]
		} else {
			result[index] = ' '
		}
		index++
	}
	return result
}

func stripCComments(source []byte) []byte {
	result := make([]byte, len(source))
	inBlock := false
	for index := 0; index < len(source); {
		if !inBlock && bytes.HasPrefix(source[index:], []byte("//")) {
			for index < len(source) && source[index] != '\n' {
				result[index] = ' '
				index++
			}
			continue
		}
		if !inBlock && bytes.HasPrefix(source[index:], []byte("/*")) {
			inBlock = true
			result[index], result[index+1] = ' ', ' '
			index += 2
			continue
		}
		if inBlock && bytes.HasPrefix(source[index:], []byte("*/")) {
			inBlock = false
			result[index], result[index+1] = ' ', ' '
			index += 2
			continue
		}
		if !inBlock || source[index] == '\n' {
			result[index] = source[index]
		} else {
			result[index] = ' '
		}
		index++
	}
	return result
}

func resolveShebang(source []byte) *Spec {
	line := string(source)
	if newline := strings.IndexByte(line, '\n'); newline >= 0 {
		line = line[:newline]
	}
	fields := strings.Fields(strings.TrimSpace(line))
	if len(fields) == 0 || !strings.HasPrefix(fields[0], "#!") {
		return nil
	}
	command := filepath.Base(strings.TrimPrefix(fields[0], "#!"))
	if command == "env" && len(fields) > 1 {
		command = fields[1]
	}
	switch command {
	case "python", "python2", "python3":
		return nameRegistry["python"]
	case "sh", "bash", "dash", "zsh", "ksh":
		return nameRegistry["bash"]
	case "ruby":
		return nameRegistry["ruby"]
	case "perl":
		return nameRegistry["perl"]
	default:
		return nil
	}
}

func hasCoqSignature(source []byte) bool {
	for _, raw := range bytes.Split(source, []byte{'\n'}) {
		line := strings.TrimSpace(string(raw))
		fields := strings.Fields(line)
		if len(fields) == 0 {
			continue
		}
		first := fields[0]
		if first == "From" && strings.Contains(line, " Require ") {
			return true
		}
		switch first {
		case "Require", "Theorem", "Lemma", "Corollary", "Proposition",
			"Definition", "Fixpoint", "CoFixpoint", "Inductive", "CoInductive",
			"Record", "Class", "Instance", "Module", "Proof.", "Qed.",
			"Defined.", "Admitted.":
			return true
		}
	}
	return false
}

func hasVerilogSignature(source []byte) bool {
	for _, raw := range bytes.Split(source, []byte{'\n'}) {
		line := strings.TrimSpace(string(raw))
		fields := strings.Fields(line)
		if len(fields) == 0 {
			continue
		}
		first := strings.TrimRight(fields[0], "#(")
		switch first {
		case "module", "interface", "package", "program", "primitive", "checker",
			"endmodule", "assign", "always", "always_ff", "always_comb", "always_latch":
			return true
		}
	}
	return false
}

func hasNginxSignature(source []byte) bool {
	for _, raw := range bytes.Split(source, []byte{'\n'}) {
		line := strings.TrimSpace(string(raw))
		if line == "" || strings.HasPrefix(line, "#") {
			continue
		}
		fields := strings.Fields(line)
		if len(fields) == 0 {
			continue
		}
		switch fields[0] {
		case "http", "events", "server", "location", "upstream", "map", "stream", "mail":
			if strings.Contains(line, "{") {
				return true
			}
		case "listen", "server_name", "proxy_pass", "fastcgi_pass", "uwsgi_pass", "include":
			if strings.HasSuffix(line, ";") {
				return true
			}
		}
	}
	return false
}

// IsFunctionNode checks if a tree-sitter node type is a function definition.
func (s *Spec) IsFunctionNode(nodeType string) bool {
	for _, t := range s.FunctionNodes {
		if t == nodeType {
			return true
		}
	}
	return false
}

// IsClassNode checks if a tree-sitter node type is a class/struct definition.
func (s *Spec) IsClassNode(nodeType string) bool {
	for _, t := range s.ClassNodes {
		if t == nodeType {
			return true
		}
	}
	return false
}

// IsCallNode checks if a tree-sitter node type is a call expression.
func (s *Spec) IsCallNode(nodeType string) bool {
	for _, t := range s.CallNodes {
		if t == nodeType {
			return true
		}
	}
	return false
}

// IsImportNode checks if a tree-sitter node type is an import statement.
func (s *Spec) IsImportNode(nodeType string) bool {
	for _, t := range s.ImportNodes {
		if t == nodeType {
			return true
		}
	}
	return false
}
