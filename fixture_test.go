package coveragefixture

import "testing"

func TestAbsolute(t *testing.T) {
	for _, input := range []int{-5, 5} {
		if got := absolute(input); got != 5 {
			t.Errorf("absolute(%d) = %d, want 5", input, got)
		}
	}
}
