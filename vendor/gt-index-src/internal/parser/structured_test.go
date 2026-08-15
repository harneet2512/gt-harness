package parser

import (
	"os"
	"testing"

	"github.com/harneet2512/groundtruth/gt-index/internal/specs"
	"github.com/harneet2512/groundtruth/gt-index/internal/walker"
)

func parseStructuredFixture(t *testing.T, language, extension, source string) *ParseResult {
	t.Helper()
	path := t.TempDir() + "/fixture" + extension
	if err := os.WriteFile(path, []byte(source), 0o600); err != nil {
		t.Fatal(err)
	}
	result, err := ParseFile(walker.SourceFile{
		Path: "fixture" + extension, AbsPath: path, Language: language,
	}, false)
	if err != nil {
		t.Fatal(err)
	}
	if result == nil {
		t.Fatalf("%s adapter returned no parse result", language)
	}
	return result
}

func TestRedcodeAdapterEmitsLabelsAndControlFlow(t *testing.T) {
	path := t.TempDir() + "/fixture.red"
	source := ";redcode-94\nstart mov 0, 1\n      jmp start\n      end start\n"
	if err := os.WriteFile(path, []byte(source), 0o600); err != nil {
		t.Fatal(err)
	}
	result, err := ParseFile(walker.SourceFile{Path: "fixture.red", AbsPath: path, Language: "red"}, false)
	if err != nil {
		t.Fatal(err)
	}
	if len(result.Nodes) != 1 || result.Nodes[0].Name != "start" {
		t.Fatalf("unexpected Redcode nodes: %+v", result.Nodes)
	}
	if len(result.Calls) != 1 || result.Calls[0].CalleeName != "start" {
		t.Fatalf("unexpected Redcode calls: %+v", result.Calls)
	}
}

func TestPOVRayAdapterEmitsMacroAndInvocation(t *testing.T) {
	path := t.TempDir() + "/fixture.pov"
	source := "#include \"shapes.inc\"\n" +
		"#macro Helper()\nsphere { <0,0,0>, 1 }\n#end\n" +
		"#macro Thing()\nHelper()\n#end\nThing()\n"
	if err := os.WriteFile(path, []byte(source), 0o600); err != nil {
		t.Fatal(err)
	}
	result, err := ParseFile(walker.SourceFile{Path: "fixture.pov", AbsPath: path, Language: "povray"}, false)
	if err != nil {
		t.Fatal(err)
	}
	if len(result.Nodes) != 2 || result.Nodes[0].Name != "Helper" || result.Nodes[1].Name != "Thing" {
		t.Fatalf("unexpected POV-Ray nodes: %+v", result.Nodes)
	}
	if len(result.Imports) != 1 || result.Imports[0].ModulePath != "shapes.inc" {
		t.Fatalf("unexpected POV-Ray imports: %+v", result.Imports)
	}
	if len(result.Calls) != 1 || result.Calls[0].CallerNodeIdx != 1 || result.Calls[0].CalleeName != "Helper" {
		t.Fatalf("unexpected POV-Ray calls: %+v", result.Calls)
	}
}

func TestCoqAdapterEmitsTheoremsAndProofReferences(t *testing.T) {
	result := parseStructuredFixture(t, "coq", ".v",
		"Require Import Arith.\n"+
			"Theorem helper : forall n : nat, n = n.\nProof. reflexivity. Qed.\n"+
			"Theorem target : forall n : nat, n = n.\nProof. intro n. apply helper. Qed.\n")
	if len(result.Nodes) != 2 || result.Nodes[0].Name != "helper" || result.Nodes[1].Name != "target" {
		t.Fatalf("unexpected Coq nodes: %+v", result.Nodes)
	}
	if len(result.Imports) != 1 || result.Imports[0].ModulePath != "Arith" {
		t.Fatalf("unexpected Coq imports: %+v", result.Imports)
	}
	if len(result.Calls) != 1 || result.Calls[0].CallerNodeIdx != 1 || result.Calls[0].CalleeName != "helper" {
		t.Fatalf("unexpected Coq references: %+v", result.Calls)
	}
}

func TestCoqAdapterDoesNotIndexDeclarationsInsideMultilineNestedComments(t *testing.T) {
	result := parseStructuredFixture(t, "coq", ".v",
		"(* outer\nTheorem fake : True. (* nested *) exact I.\n*)\n"+
			"Theorem real : True.\nProof. exact I. Qed.\n")
	if len(result.Nodes) != 1 || result.Nodes[0].Name != "real" {
		t.Fatalf("commented Coq declaration leaked into graph: %+v", result.Nodes)
	}
}

func TestCOBOLParserEmitsParagraphCallReference(t *testing.T) {
	path := t.TempDir() + "/fixture.cbl"
	source := "       IDENTIFICATION DIVISION.\n" +
		"       PROGRAM-ID. FIXTURE.\n" +
		"       PROCEDURE DIVISION.\n" +
		"       MAIN-PARA.\n" +
		"           PERFORM HELPER-PARA.\n" +
		"           STOP RUN.\n" +
		"       HELPER-PARA.\n" +
		"           DISPLAY \"ok\".\n"
	if err := os.WriteFile(path, []byte(source), 0o600); err != nil {
		t.Fatal(err)
	}
	result, err := ParseFile(walker.SourceFile{
		Path: "fixture.cbl", AbsPath: path, Language: "cobol", Spec: specs.ForExtension(".cbl"),
	}, false)
	if err != nil {
		t.Fatal(err)
	}
	if len(result.Calls) != 1 || result.Calls[0].CalleeName != "HELPER-PARA" {
		t.Fatalf("COBOL paragraph call missing: nodes=%+v calls=%+v", result.Nodes, result.Calls)
	}
}

func TestRParserEmitsLocalCallReference(t *testing.T) {
	path := t.TempDir() + "/fixture.r"
	source := "r_target <- function(value) { value + 1 }\n" +
		"r_caller <- function() { r_target(1) }\n"
	if err := os.WriteFile(path, []byte(source), 0o600); err != nil {
		t.Fatal(err)
	}
	result, err := ParseFile(walker.SourceFile{
		Path: "fixture.r", AbsPath: path, Language: "r", Spec: specs.ForExtension(".r"),
	}, false)
	if err != nil {
		t.Fatal(err)
	}
	if len(result.Nodes) != 2 || result.Nodes[0].Name != "r_target" || result.Nodes[1].Name != "r_caller" || len(result.Calls) != 1 || result.Calls[0].CallerNodeIdx != 1 || result.Calls[0].CalleeName != "r_target" || result.Calls[0].CalleeQualified != "r_target" {
		t.Fatalf("R local call missing: nodes=%+v calls=%+v", result.Nodes, result.Calls)
	}
}

func TestBenchmarkStructuredAdaptersEmitConcreteAnchors(t *testing.T) {
	tests := []struct {
		language  string
		extension string
		source    string
		wantNode  string
	}{
		{"stan", ".stan", "functions { real helper(real x) { return x; } real target(real x) { return helper(x); } }\n", "helper"},
		{"turtle", ".ttl", "@prefix ex: <https://example.test/> .\nex:University a ex:Organization .\n", "ex:University"},
		{"latex", ".tex", "\\newcommand{\\helper}[1]{#1}\n\\newcommand{\\target}[1]{\\helper{#1}}\n", "helper"},
		{"vim", ".vim", "function! Helper()\nendfunction\nfunction! Target()\n  call Helper()\nendfunction\n", "Helper"},
		{"nginx", ".conf", "upstream backend { server 127.0.0.1:8080; }\nserver { location / { proxy_pass http://backend; } }\n", "backend"},
		{"gcode", ".gcode", "O1000\nG1 X1\nM98 P2000\nM30\nO2000\nM99\n", "O1000"},
		{"make", "Makefile", "all: build\nbuild:\n\t@true\n", "all"},
		{"dockerfile", "Dockerfile", "FROM python:3.12 AS runtime\nRUN python -V\n", "runtime"},
		{"cmake", "CMakeLists.txt", "function(helper)\nendfunction()\nfunction(target)\n  helper()\nendfunction()\n", "helper"},
		{"meson", "meson.build", "project('example', 'c')\nexecutable('app', 'main.c')\n", "app"},
		{"autotools", "configure.ac", "AC_INIT([example], [1.0])\nAC_CONFIG_FILES([Makefile])\n", "example"},
		{"objective_c", ".m", "@interface Worker : NSObject\n- (void)run;\n@end\n@implementation Worker\n- (void)run { }\n@end\n", "run"},
	}
	for _, test := range tests {
		t.Run(test.language, func(t *testing.T) {
			result := parseStructuredFixture(t, test.language, test.extension, test.source)
			found := false
			for _, node := range result.Nodes {
				if node.Name == test.wantNode {
					found = true
				}
			}
			if !found {
				t.Fatalf("%s missing concrete node %q: %+v", test.language, test.wantNode, result.Nodes)
			}
		})
	}
}

func TestSPARQLAdapterRetainsFileAndPrefixesWithoutInventingCallableSymbols(t *testing.T) {
	result := parseStructuredFixture(t, "sparql", ".sparql",
		"PREFIX ex: <https://example.test/>\nSELECT ?s WHERE { ?s ex:name ?name . }\n")
	if len(result.Nodes) != 1 || result.Nodes[0].Label != "File" || result.Nodes[0].Name != "fixture.sparql" {
		t.Fatalf("SPARQL must expose only a concrete file node: %+v", result.Nodes)
	}
	if len(result.Imports) != 1 || result.Imports[0].ImportedName != "ex" {
		t.Fatalf("SPARQL prefix missing: %+v", result.Imports)
	}
}

func TestStructuredAdaptersRetainConcreteFileOrBlockAnchorsWithoutFunctions(t *testing.T) {
	stan := parseStructuredFixture(t, "stan", ".stan",
		"data { int<lower=0> N; }\nparameters { real mu; }\nmodel { mu ~ normal(0, 1); }\n")
	stanNames := map[string]bool{}
	for _, node := range stan.Nodes {
		stanNames[node.Name] = true
	}
	if !stanNames["parameters"] || !stanNames["model"] {
		t.Fatalf("Stan block anchors missing: %+v", stan.Nodes)
	}

	vim := parseStructuredFixture(t, "vim", ".vim",
		"call setreg('a', \"dw\")\n:%normal! @a\n:wq\n")
	if len(vim.Nodes) == 0 || vim.Nodes[0].Name != "register_a" || vim.Nodes[0].Label != "Type" {
		t.Fatalf("Vim register anchor missing: %+v", vim.Nodes)
	}

	gcode := parseStructuredFixture(t, "gcode", ".gcode", "G1 X1.0 Y2.0\nG1 X2.0 Y3.0\n")
	if len(gcode.Nodes) != 1 || gcode.Nodes[0].Label != "File" {
		t.Fatalf("G-code file anchor missing: %+v", gcode.Nodes)
	}
}
