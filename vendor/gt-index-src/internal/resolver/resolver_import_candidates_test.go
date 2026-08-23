package resolver

import "testing"

func TestUniqueImportCandidatesDoesNotCountSameResolvedSymbolTwice(t *testing.T) {
	got := uniqueImportCandidates([]int64{41, 41, 0, 52, 41, 52})
	want := []int64{41, 52}
	if len(got) != len(want) {
		t.Fatalf("got %v, want %v", got, want)
	}
	for i := range want {
		if got[i] != want[i] {
			t.Fatalf("got %v, want %v", got, want)
		}
	}
}
