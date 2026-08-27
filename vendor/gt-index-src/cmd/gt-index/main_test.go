package main

import (
	"testing"

	"github.com/harneet2512/groundtruth/gt-index/internal/resolver"
)

func TestResolvedCallVerificationRequiresStructuralProof(t *testing.T) {
	tests := []struct {
		name string
		call resolver.ResolvedCall
		want string
	}{
		{
			name: "same file exact",
			call: resolver.ResolvedCall{Method: "same_file", Confidence: 1, CandidateCount: 1, TrustTier: "CERTIFIED"},
			want: "verified",
		},
		{
			name: "import exact",
			call: resolver.ResolvedCall{Method: "import", Confidence: 1, CandidateCount: 1, TrustTier: "CERTIFIED"},
			want: "verified",
		},
		{
			name: "typed receiver exact",
			call: resolver.ResolvedCall{Method: "type_flow", Confidence: .95, CandidateCount: 1, TrustTier: "CERTIFIED", ReceiverType: "Loader"},
			want: "verified",
		},
		{
			name: "receiver proof missing",
			call: resolver.ResolvedCall{Method: "type_flow", Confidence: .95, CandidateCount: 1, TrustTier: "CERTIFIED"},
			want: "unverified",
		},
		{
			name: "unique name is not call proof",
			call: resolver.ResolvedCall{Method: "verified_unique", Confidence: .95, CandidateCount: 1, TrustTier: "CERTIFIED"},
			want: "unverified",
		},
		{
			name: "ambiguous import",
			call: resolver.ResolvedCall{Method: "import", Confidence: 1, CandidateCount: 2, TrustTier: "CERTIFIED"},
			want: "unverified",
		},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			if got := resolvedCallVerificationStatus(test.call); got != test.want {
				t.Fatalf("resolvedCallVerificationStatus() = %q, want %q", got, test.want)
			}
		})
	}
}
