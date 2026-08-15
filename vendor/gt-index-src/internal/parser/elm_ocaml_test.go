package parser

import (
	"os"
	"strings"
	"testing"

	"github.com/harneet2512/groundtruth/gt-index/internal/specs"
	"github.com/harneet2512/groundtruth/gt-index/internal/walker"
)

// TestElmValueDeclarationNamesAreBareIdentifiers guards the Elm regression:
// `target value = ...` parses as value_declaration -> function_declaration_left
// -> lower_case_identifier. The spec has no NameField and the identifier is
// not a direct child, so names were silently dropped (0 definitions -> 0 CALLS
// edges). functionNodeName now descends the declaration-left wrapper.
func TestElmValueDeclarationNamesAreBareIdentifiers(t *testing.T) {
	path := t.TempDir() + "/fixture.elm"
	source := "target value =\n    value + 1\n\ncaller =\n    target 1\n"
	if err := os.WriteFile(path, []byte(source), 0o600); err != nil {
		t.Fatal(err)
	}
	result, err := ParseFile(walker.SourceFile{
		Path: "fixture.elm", AbsPath: path, Language: "elm",
		Spec: specs.ForExtension(".elm"),
	}, false)
	if err != nil {
		t.Fatal(err)
	}
	if result == nil {
		t.Fatal("elm returned no parse result")
	}
	if len(result.Nodes) != 2 || result.Nodes[0].Name != "target" || result.Nodes[1].Name != "caller" {
		t.Fatalf("unexpected elm nodes: %+v", result.Nodes)
	}
	for _, n := range result.Nodes {
		if strings.ContainsAny(n.Name, "()") {
			t.Fatalf("elm node name carries signature text: %q", n.Name)
		}
	}
	var found bool
	for _, call := range result.Calls {
		if call.CalleeName == "target" {
			found = true
			if call.CallerNodeIdx != 1 {
				t.Fatalf("elm call CallerNodeIdx: want 1 got %d", call.CallerNodeIdx)
			}
		}
	}
	if !found {
		t.Fatalf("elm call to target missing: %+v", result.Calls)
	}
}

// TestOCamlLetBindingNamesAreBareIdentifiers guards the OCaml regression:
// `let target value = ...` nests the name in a value_name wrapper child, which
// extractFirstIdentifier cannot reach. functionNodeName now descends to the
// first value_name at the let_binding level.
func TestOCamlLetBindingNamesAreBareIdentifiers(t *testing.T) {
	path := t.TempDir() + "/fixture.ml"
	source := "let target value = value + 1\nlet caller () = target 1\n"
	if err := os.WriteFile(path, []byte(source), 0o600); err != nil {
		t.Fatal(err)
	}
	result, err := ParseFile(walker.SourceFile{
		Path: "fixture.ml", AbsPath: path, Language: "ocaml",
		Spec: specs.ForExtension(".ml"),
	}, false)
	if err != nil {
		t.Fatal(err)
	}
	if result == nil {
		t.Fatal("ocaml returned no parse result")
	}
	if len(result.Nodes) != 2 || result.Nodes[0].Name != "target" || result.Nodes[1].Name != "caller" {
		t.Fatalf("unexpected ocaml nodes: %+v", result.Nodes)
	}
	var found bool
	for _, call := range result.Calls {
		if call.CalleeName == "target" {
			found = true
			if call.CallerNodeIdx != 1 {
				t.Fatalf("ocaml call CallerNodeIdx: want 1 got %d", call.CallerNodeIdx)
			}
		}
	}
	if !found {
		t.Fatalf("ocaml call to target missing: %+v", result.Calls)
	}
}
