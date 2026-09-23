package main

import (
	"testing"

	"github.com/stretchr/testify/assert"
)

func TestCompute(t *testing.T) {
	assert.Equal(t, 4, Compute(1, 2))
}

func TestGreet(t *testing.T) {
	g := FriendlyGreeter{Prefix: "hi"}
	if got := g.Greet("ada"); got != "hi ada" {
		t.Fatalf("Greet(ada) = %q, want %q", got, "hi ada")
	}
}
