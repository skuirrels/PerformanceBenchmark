package perfbench

import (
	"fmt"
	"testing"
)

func BenchmarkHashMap1000(b *testing.B) {
	keys := makeKeys(1000)
	b.ResetTimer()
	for i := 0; i < b.N; i++ {
		_ = buildAndLookup(keys)
	}
}

func BenchmarkHashMap100000(b *testing.B) {
	keys := makeKeys(100000)
	b.ResetTimer()
	for i := 0; i < b.N; i++ {
		_ = buildAndLookup(keys)
	}
}

func makeKeys(count int) []string {
	keys := make([]string, count)
	for i := range keys {
		keys[i] = fmt.Sprintf("key-%06d", i)
	}
	return keys
}

func buildAndLookup(keys []string) int64 {
	values := make(map[string]int, len(keys))
	for i, key := range keys {
		values[key] = i
	}

	var sum int64
	for i := len(keys) - 1; i >= 0; i-- {
		sum += int64(values[keys[i]])
	}
	return sum
}
