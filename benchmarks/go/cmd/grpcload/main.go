package main

import (
	"context"
	"encoding/json"
	"flag"
	"fmt"
	"os"
	"sort"
	"sync"
	"sync/atomic"
	"time"

	"dev/perfbench/benchmarks/go/internal/perfpb"
	"google.golang.org/grpc"
	"google.golang.org/grpc/credentials/insecure"
	"google.golang.org/protobuf/proto"
)

type result struct {
	Address                     string             `json:"address"`
	Method                      string             `json:"method"`
	Concurrency                 int                `json:"concurrency"`
	DurationSeconds             float64            `json:"durationSeconds"`
	WarmupSeconds               float64            `json:"warmupSeconds"`
	Requests                    int64              `json:"requests"`
	Failures                    int64              `json:"failures"`
	ResponseBytes               int64              `json:"responseBytes"`
	ThroughputRequestsPerSecond float64            `json:"throughputRequestsPerSecond"`
	LatencyNs                   map[string]float64 `json:"latencyNs"`
	ErrorCounts                 map[string]int64   `json:"errorCounts,omitempty"`
}

func main() {
	address := flag.String("address", "", "target host:port")
	concurrency := flag.Int("concurrency", 16, "concurrent workers")
	duration := flag.Duration("duration", 10*time.Second, "measurement duration")
	warmup := flag.Duration("warmup", 5*time.Second, "warmup duration")
	flag.Parse()

	if *address == "" {
		fmt.Fprintln(os.Stderr, "--address is required")
		os.Exit(2)
	}

	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	connection, err := grpc.DialContext(ctx, *address, grpc.WithTransportCredentials(insecure.NewCredentials()), grpc.WithBlock())
	if err != nil {
		fmt.Fprintf(os.Stderr, "failed to connect: %v\n", err)
		os.Exit(1)
	}
	defer connection.Close()

	client := perfpb.NewQuoteServiceClient(connection)
	if *warmup > 0 {
		_ = run(client, *address, *concurrency, *warmup, false)
	}
	measured := run(client, *address, *concurrency, *duration, true)
	measured.WarmupSeconds = warmup.Seconds()

	encoder := json.NewEncoder(os.Stdout)
	encoder.SetIndent("", "  ")
	if err := encoder.Encode(measured); err != nil {
		fmt.Fprintf(os.Stderr, "failed to encode result: %v\n", err)
		os.Exit(1)
	}

	if measured.Failures > 0 || measured.Requests == 0 {
		os.Exit(1)
	}
}

func run(client perfpb.QuoteServiceClient, address string, concurrency int, duration time.Duration, collect bool) result {
	var requests int64
	var failures int64
	var responseBytes int64
	errorCounts := map[string]int64{}
	var errorLock sync.Mutex
	latencies := make([]int64, 0, 1024)
	var latencyLock sync.Mutex
	var wg sync.WaitGroup
	started := time.Now()
	stopAt := started.Add(duration)

	for worker := 0; worker < concurrency; worker++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			localLatencies := make([]int64, 0, 1024)
			for time.Now().Before(stopAt) {
				callCtx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
				start := time.Now()
				response, err := client.Quote(callCtx, &perfpb.QuoteRequest{
					CustomerId: "customer-42",
					ItemCount:  12,
					UnitPrice:  19.95,
					Expedited:  true,
				})
				cancel()
				elapsed := time.Since(start).Nanoseconds()
				if err != nil {
					atomic.AddInt64(&failures, 1)
					addError(errorCounts, &errorLock, err.Error())
					continue
				}
				if response == nil || !response.GetAccepted() || !response.GetExpedited() || response.GetCustomerId() != "customer-42" {
					atomic.AddInt64(&failures, 1)
					addError(errorCounts, &errorLock, "unexpected response")
					continue
				}
				atomic.AddInt64(&requests, 1)
				atomic.AddInt64(&responseBytes, int64(proto.Size(response)))
				if collect {
					localLatencies = append(localLatencies, elapsed)
				}
			}
			if collect && len(localLatencies) > 0 {
				latencyLock.Lock()
				latencies = append(latencies, localLatencies...)
				latencyLock.Unlock()
			}
		}()
	}
	wg.Wait()

	elapsedSeconds := time.Since(started).Seconds()
	if elapsedSeconds <= 0 {
		elapsedSeconds = 0.001
	}

	return result{
		Address:                     address,
		Method:                      "gRPC",
		Concurrency:                 concurrency,
		DurationSeconds:             elapsedSeconds,
		Requests:                    requests,
		Failures:                    failures,
		ResponseBytes:               responseBytes,
		ThroughputRequestsPerSecond: float64(requests) / elapsedSeconds,
		LatencyNs:                   summarizeLatency(latencies),
		ErrorCounts:                 errorCounts,
	}
}

func addError(counts map[string]int64, lock *sync.Mutex, value string) {
	lock.Lock()
	defer lock.Unlock()
	counts[value]++
}

func summarizeLatency(values []int64) map[string]float64 {
	if len(values) == 0 {
		return map[string]float64{"sampleCount": 0}
	}
	sort.Slice(values, func(i, j int) bool { return values[i] < values[j] })
	var total int64
	for _, value := range values {
		total += value
	}
	return map[string]float64{
		"sampleCount": float64(len(values)),
		"mean":        float64(total) / float64(len(values)),
		"median":      percentile(values, 50),
		"p95":         percentile(values, 95),
		"min":         float64(values[0]),
		"max":         float64(values[len(values)-1]),
	}
}

func percentile(values []int64, pct float64) float64 {
	if len(values) == 1 {
		return float64(values[0])
	}
	rank := (float64(len(values)-1) * pct) / 100
	lower := int(rank)
	upper := lower
	if float64(lower) != rank {
		upper = lower + 1
	}
	if lower == upper {
		return float64(values[lower])
	}
	weight := rank - float64(lower)
	return float64(values[lower])*(1-weight) + float64(values[upper])*weight
}
