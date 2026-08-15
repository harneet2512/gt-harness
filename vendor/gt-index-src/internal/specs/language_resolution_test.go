package specs

import "testing"

func TestResolveSourceDisambiguatesCoqAndVerilog(t *testing.T) {
	coq, coqReason := ResolveSource("partial_proof.v", []byte(
		"Require Import Arith.\nTheorem plus_comm : forall n m : nat, n + m = m + n.\nProof. Qed.\n"))
	if coq == nil || coq.Name != "coq" || coqReason != "content_signature_coq" {
		t.Fatalf("unexpected Coq resolution: spec=%v reason=%q", coq, coqReason)
	}
	verilog, verilogReason := ResolveSource("adder.v", []byte(
		"module adder(input a, output b); assign b = a; endmodule\n"))
	if verilog == nil || verilog.Name != "verilog" || verilogReason != "content_signature_verilog" {
		t.Fatalf("unexpected Verilog resolution: spec=%v reason=%q", verilog, verilogReason)
	}
	unknown, reason := ResolveSource("unknown.v", []byte("(* no declaration *)\n"))
	if unknown != nil || reason != "ambiguous_extension" {
		t.Fatalf("ambiguous .v must abstain: spec=%v reason=%q", unknown, reason)
	}
}

func TestResolveSourceIgnoresCommentedVDeclarations(t *testing.T) {
	for _, source := range []string{
		"(* outer\nTheorem fake : True. (* nested *) exact I.\n*)\n",
		"// module fake;\n/* module also_fake; endmodule */\n",
	} {
		spec, reason := ResolveSource("commented.v", []byte(source))
		if spec != nil || reason != "ambiguous_extension" {
			t.Fatalf("commented declaration selected parser: spec=%v reason=%q", spec, reason)
		}
	}
}

func TestResolveSourceCoversBenchmarkLanguageExtensions(t *testing.T) {
	fixtures := map[string]struct{ language, source string }{
		"model.stan":           {"stan", "parameters { real mu; }\n"},
		"solution.sparql":      {"sparql", "SELECT ?s WHERE { ?s ?p ?o . }\n"},
		"university_graph.ttl": {"turtle", "@prefix ex: <https://example/> .\n"},
		"input.tex":            {"latex", "\\documentclass{article}\n"},
		"apply_macros.vim":     {"vim", "function! Apply()\nendfunction\n"},
		"benchmark-site.conf":  {"nginx", "server { listen 8080; }\n"},
		"text.gcode":           {"gcode", "G1 X1 Y1\n"},
	}
	for path, fixture := range fixtures {
		spec, reason := ResolveSource(path, []byte(fixture.source))
		wantReason := "unique_extension"
		if fixture.language == "nginx" {
			wantReason = "content_signature_nginx"
		}
		if spec == nil || spec.Name != fixture.language || reason != wantReason {
			t.Fatalf("%s: spec=%v reason=%q want=%s", path, spec, reason, fixture.language)
		}
	}
}

func TestNginxResolutionUsesTheSameContentSignatureReasonAsTheHost(t *testing.T) {
	spec, reason := ResolveSource("site.conf", []byte("server { listen 8080; }\n"))
	if spec == nil || spec.Name != "nginx" || reason != "content_signature_nginx" {
		t.Fatalf("unexpected Nginx resolution: spec=%v reason=%q", spec, reason)
	}
}

func TestResolveSourceUsesBasenamesAndShebangs(t *testing.T) {
	fixtures := map[string]struct{ language, source string }{
		"Makefile":       {"make", "all: build\nbuild:\n\t@true\n"},
		"Dockerfile":     {"dockerfile", "FROM python:3.12 AS runtime\n"},
		"CMakeLists.txt": {"cmake", "project(example)\n"},
		"meson.build":    {"meson", "project('example', 'c')\n"},
		"configure.ac":   {"autotools", "AC_INIT([example], [1.0])\n"},
		"script":         {"python", "#!/usr/bin/env python3\nprint('ok')\n"},
	}
	for path, fixture := range fixtures {
		spec, _ := ResolveSource(path, []byte(fixture.source))
		if spec == nil || spec.Name != fixture.language {
			t.Fatalf("%s resolved as %v, want %s", path, spec, fixture.language)
		}
	}
}

func TestResolutionNeedsContentOnlyForAmbiguousBroadOrShebangPaths(t *testing.T) {
	for _, test := range []struct {
		path string
		want bool
	}{
		{"main.py", false},
		{"proof.v", true},
		{"site.conf", true},
		{"runner", true},
		{"Makefile", false},
	} {
		if got := ResolutionNeedsContent(test.path); got != test.want {
			t.Fatalf("ResolutionNeedsContent(%q)=%v, want %v", test.path, got, test.want)
		}
	}
}
