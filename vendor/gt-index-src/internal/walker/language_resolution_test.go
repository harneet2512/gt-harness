package walker

import (
	"os"
	"path/filepath"
	"testing"
)

func TestWalkResolvesContentBasenamesAndShebangsWithoutGuessing(t *testing.T) {
	root := t.TempDir()
	fixtures := map[string]string{
		"proof.v":      "Require Import Arith.\nTheorem target : forall n : nat, n = n.\nProof. Qed.\n",
		"adder.v":      "module adder(input a, output b); assign b = a; endmodule\n",
		"ambiguous.v":  "(* no language-bearing declaration *)\n",
		"nginx.conf":   "server { listen 8080; }\n",
		"generic.conf": "key=value\n",
		"Makefile":     "all: build\nbuild:\n\t@true\n",
		"script":       "#!/usr/bin/env python3\nprint('ok')\n",
		"plain-binary": "not a shebang script\n",
	}
	for name, source := range fixtures {
		if err := os.WriteFile(filepath.Join(root, name), []byte(source), 0o600); err != nil {
			t.Fatal(err)
		}
	}
	files, err := Walk(root, 100)
	if err != nil {
		t.Fatal(err)
	}
	languages := map[string]string{}
	for _, file := range files {
		languages[file.Path] = file.Language
	}
	for path, want := range map[string]string{
		"proof.v": "coq", "adder.v": "verilog", "nginx.conf": "nginx",
		"Makefile": "make", "script": "python",
	} {
		if languages[path] != want {
			t.Fatalf("%s resolved as %q, want %q (all=%v)", path, languages[path], want, languages)
		}
	}
	for _, path := range []string{"ambiguous.v", "generic.conf", "plain-binary"} {
		if _, present := languages[path]; present {
			t.Fatalf("%s must abstain, got %q", path, languages[path])
		}
	}
}
