package specs

import (
	"strings"
	"testing"

	sitter "github.com/smacker/go-tree-sitter"
)

func parseCertified(t *testing.T, spec *Spec, source string) string {
	t.Helper()
	if spec == nil || spec.Language == nil {
		t.Fatalf("language spec is not registered")
	}
	p := sitter.NewParser()
	t.Cleanup(p.Close)
	p.SetLanguage(spec.Language)
	tree := p.Parse(nil, []byte(source))
	t.Cleanup(tree.Close)
	return tree.RootNode().String()
}

func TestCertifiedCobolGrammarEmitsProceduresAndPerform(t *testing.T) {
	tree := parseCertified(t, ForExtension(".cbl"),
		"       IDENTIFICATION DIVISION.\n"+
			"       PROGRAM-ID. FIXTURE.\n"+
			"       PROCEDURE DIVISION.\n"+
			"       MAIN-PARA.\n"+
			"           PERFORM HELPER-PARA.\n"+
			"       HELPER-PARA.\n"+
			"           STOP RUN.\n")
	for _, nodeType := range []string{"program_definition", "procedure_division", "paragraph_header", "perform_statement_call_proc"} {
		if !strings.Contains(tree, nodeType) {
			t.Fatalf("COBOL tree missing %q: %s", nodeType, tree)
		}
	}
}

func TestCertifiedSchemeGrammarEmitsDefinitionsAndCalls(t *testing.T) {
	tree := parseCertified(t, ForExtension(".scm"),
		"(define (target value) (+ value 1))\n"+
			"(define (caller) (target 1))\n")
	for _, nodeType := range []string{"binding_procedure", "procedure_call"} {
		if !strings.Contains(tree, nodeType) {
			t.Fatalf("Scheme tree missing %q: %s", nodeType, tree)
		}
	}
}

func TestCertifiedRGrammarEmitsDefinitionsAndCalls(t *testing.T) {
	tree := parseCertified(t, ForExtension(".r"),
		"target <- function(value) { value + 1 }\n"+
			"caller <- function() { target(1) }\n")
	for _, nodeType := range []string{"function_definition", "call"} {
		if !strings.Contains(tree, nodeType) {
			t.Fatalf("R tree missing %q: %s", nodeType, tree)
		}
	}
}

func TestCertifiedVerilogGrammarEmitsModulesAndInstantiation(t *testing.T) {
	source := "module target(input value, output out);\n" +
		"  assign out = value;\n" +
		"endmodule\n" +
		"module caller(input value, output out);\n" +
		"  target instance(.value(value), .out(out));\n" +
		"endmodule\n"
	spec, _ := ResolveSource("fixture.v", []byte(source))
	tree := parseCertified(t, spec, source)
	for _, nodeType := range []string{"module_declaration", "module_instantiation"} {
		if !strings.Contains(tree, nodeType) {
			t.Fatalf("Verilog tree missing %q: %s", nodeType, tree)
		}
	}
}
