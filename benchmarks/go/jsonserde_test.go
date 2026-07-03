package perfbench

import (
	"encoding/json"
	"testing"
)

type payload struct {
	ID     int     `json:"id"`
	Name   string  `json:"name"`
	Active bool    `json:"active"`
	Score  float64 `json:"score"`
}

func BenchmarkJsonSerdeSerialize100(b *testing.B) {
	values := makePayloads(100)
	b.ResetTimer()
	for i := 0; i < b.N; i++ {
		_, _ = json.Marshal(values)
	}
}

func BenchmarkJsonSerdeSerialize1000(b *testing.B) {
	values := makePayloads(1000)
	b.ResetTimer()
	for i := 0; i < b.N; i++ {
		_, _ = json.Marshal(values)
	}
}

func BenchmarkJsonSerdeDeserialize100(b *testing.B) {
	data, _ := json.Marshal(makePayloads(100))
	b.ResetTimer()
	for i := 0; i < b.N; i++ {
		var values []payload
		_ = json.Unmarshal(data, &values)
	}
}

func BenchmarkJsonSerdeDeserialize1000(b *testing.B) {
	data, _ := json.Marshal(makePayloads(1000))
	b.ResetTimer()
	for i := 0; i < b.N; i++ {
		var values []payload
		_ = json.Unmarshal(data, &values)
	}
}

func makePayloads(count int) []payload {
	values := make([]payload, count)
	for i := range values {
		values[i] = payload{
			ID:     i,
			Name:   "item-" + itoa(i),
			Active: i%7 == 0,
			Score:  float64(i) * 1.25,
		}
	}
	return values
}

func itoa(value int) string {
	if value == 0 {
		return "0"
	}

	var buf [20]byte
	i := len(buf)
	for value > 0 {
		i--
		buf[i] = byte('0' + value%10)
		value /= 10
	}
	return string(buf[i:])
}
