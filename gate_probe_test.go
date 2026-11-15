package probe

import "testing"

func TestDeliberateFixtureFailure(t *testing.T) {
	t.Fatal("intentional isolated test-gate validation")
}
