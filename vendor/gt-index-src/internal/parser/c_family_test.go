package parser

import (
	"os"
	"strings"
	"testing"

	"github.com/harneet2512/groundtruth/gt-index/internal/specs"
	"github.com/harneet2512/groundtruth/gt-index/internal/walker"
)

// TestCFamilyFunctionNamesAreBareIdentifiers guards the C/C++ declarator
// regression: tree-sitter-c/cpp expose the function name only through the
// NameField "declarator" wrapper (function_declarator -> identifier), so the
// naive extractFieldText produced names like `get_bit(int ctx)`. The resolver
// binds bare call callees to node names, so a signature-laden name silently
// killed every CALLS edge (write-compressor: 18 C nodes, 0 edges). Node names
// must be the bare identifier and the fixture call must resolve by name.
func TestCFamilyFunctionNamesAreBareIdentifiers(t *testing.T) {
	source := "int target(int value) { return value + 1; }\nint caller() { return target(1); }\n"
	for _, tc := range []struct {
		language string
		ext      string
	}{
		{"c", ".c"},
		{"cpp", ".cpp"},
	} {
		path := t.TempDir() + "/fixture" + tc.ext
		if err := os.WriteFile(path, []byte(source), 0o600); err != nil {
			t.Fatal(err)
		}
		result, err := ParseFile(walker.SourceFile{
			Path: "fixture" + tc.ext, AbsPath: path, Language: tc.language,
			Spec: specs.ForExtension(tc.ext),
		}, false)
		if err != nil {
			t.Fatalf("%s parse: %v", tc.language, err)
		}
		if result == nil {
			t.Fatalf("%s returned no parse result", tc.language)
		}
		if len(result.Nodes) != 2 {
			t.Fatalf("%s nodes: want 2 got %d: %+v", tc.language, len(result.Nodes), result.Nodes)
		}
		for i, want := range []string{"target", "caller"} {
			if got := result.Nodes[i].Name; got != want {
				t.Fatalf("%s node %d name: want %q got %q", tc.language, i, want, got)
			}
			if strings.ContainsAny(result.Nodes[i].Name, "()") {
				t.Fatalf("%s node %d name carries signature text: %q", tc.language, i, result.Nodes[i].Name)
			}
		}
		var found bool
		for _, call := range result.Calls {
			if call.CalleeName == "target" {
				found = true
				// caller is the second node -> zero-based CallerNodeIdx 1.
				if call.CallerNodeIdx != 1 {
					t.Fatalf("%s call CallerNodeIdx: want 1 got %d", tc.language, call.CallerNodeIdx)
				}
			}
		}
		if !found {
			t.Fatalf("%s call to target missing: %+v", tc.language, result.Calls)
		}
	}
}

// TestBashFunctionCallResolution ensures same-file literal function invocation
// is extracted as a call to the defined function (bash resolves a bare command
// to a function before PATH lookup), while builtin/external commands like
// `return` do not become callee candidates that pollute resolution.
func TestBashFunctionCallResolution(t *testing.T) {
	path := t.TempDir() + "/fixture.sh"
	source := "target() { return 0; }\ncaller() { target; }\n"
	if err := os.WriteFile(path, []byte(source), 0o600); err != nil {
		t.Fatal(err)
	}
	result, err := ParseFile(walker.SourceFile{
		Path: "fixture.sh", AbsPath: path, Language: "bash",
		Spec: specs.ForExtension(".sh"),
	}, false)
	if err != nil {
		t.Fatal(err)
	}
	if result == nil {
		t.Fatal("bash returned no parse result")
	}
	if len(result.Nodes) != 2 || result.Nodes[0].Name != "target" || result.Nodes[1].Name != "caller" {
		t.Fatalf("unexpected bash nodes: %+v", result.Nodes)
	}
	var found bool
	for _, call := range result.Calls {
		if call.CalleeName == "target" {
			found = true
			if call.CallerNodeIdx != 1 {
				t.Fatalf("bash call CallerNodeIdx: want 1 got %d", call.CallerNodeIdx)
			}
		}
	}
	if !found {
		t.Fatalf("bash call to target missing: %+v", result.Calls)
	}
}
