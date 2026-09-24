package main

import (
	"fmt"
	"net/http"
)

// GoGreeter is the fixture interface; FriendlyGreeter satisfies it
// structurally (Go CHA method-set implementation, no declaration).
type GoGreeter interface {
	Greet(name string) string
}

type FriendlyGreeter struct {
	Prefix string
}

func (g FriendlyGreeter) Greet(name string) string {
	return joinParts(g.Prefix, name)
}

func joinParts(a string, b string) string {
	return a + " " + b
}

func helper(n int) int {
	x := n * 2
	if x > 10 {
		x = x - 1
	}
	return x
}

func Compute(a int, b int) int {
	y := a + b
	z := helper(y)
	return z
}

func itemsHandler(w http.ResponseWriter, r *http.Request) {
	g := FriendlyGreeter{Prefix: "hi"}
	name := r.URL.Query().Get("name")
	fmt.Fprint(w, g.Greet(name))
}

func main() {
	mux := http.NewServeMux()
	mux.HandleFunc("/api/items", itemsHandler)
	_ = http.ListenAndServe(":8080", mux)
}
