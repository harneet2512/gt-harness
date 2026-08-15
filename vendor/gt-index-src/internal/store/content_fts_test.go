//go:build sqlite_fts5

package store

import (
	"os"
	"path/filepath"
	"testing"
)

func TestPopulateContentFTSIndexesTestBodiesForTypedValidationRetrieval(t *testing.T) {
	root := t.TempDir()
	testPath := filepath.Join(root, "tests", "test_widget.go")
	if err := os.MkdirAll(filepath.Dir(testPath), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(
		testPath,
		[]byte("func TestEmptyDefault(t *testing.T) {\n\tassertQuotedEmptyDefault()\n}\n"),
		0o644,
	); err != nil {
		t.Fatal(err)
	}
	db, err := Open(filepath.Join(t.TempDir(), "graph.db"))
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()
	if err := db.EnsureContentFTS(); err != nil {
		t.Fatal(err)
	}
	id, err := db.InsertNode(&Node{
		Label:     "Function",
		Name:      "TestEmptyDefault",
		FilePath:  "tests/test_widget.go",
		StartLine: 1,
		EndLine:   3,
		IsTest:    true,
		Language:  "go",
	})
	if err != nil {
		t.Fatal(err)
	}
	if err := db.PopulateContentFTS(root); err != nil {
		t.Fatal(err)
	}
	var content string
	if err := db.db.QueryRow(
		"SELECT content FROM symbol_content_fts WHERE rowid = ?", id,
	).Scan(&content); err != nil {
		t.Fatalf("test body was not indexed: %v", err)
	}
	if content == "" {
		t.Fatal("test body content must be non-empty")
	}
}
