package main

import (
	"context"
	"crypto/tls"
	"encoding/json"
	"flag"
	"fmt"
	"io"
	"net/http"
	"os"
	"sort"
	"strings"
	"sync"
	"sync/atomic"
	"time"
)

type result struct {
	URL                         string             `json:"url"`
	Method                      string             `json:"method"`
	Concurrency                 int                `json:"concurrency"`
	DurationSeconds             float64            `json:"durationSeconds"`
	WarmupSeconds               float64            `json:"warmupSeconds"`
	Requests                    int64              `json:"requests"`
	Failures                    int64              `json:"failures"`
	ResponseBytes               int64              `json:"responseBytes"`
	ThroughputRequestsPerSecond float64            `json:"throughputRequestsPerSecond"`
	LatencyNs                   map[string]float64 `json:"latencyNs"`
}

func main() {
	url := flag.String("url", "", "target URL")
	method := flag.String("method", "GET", "HTTP method")
	concurrency := flag.Int("concurrency", 16, "concurrent workers")
	duration := flag.Duration("duration", 10*time.Second, "measurement duration")
	warmup := flag.Duration("warmup", 5*time.Second, "warmup duration")
	expectedStatus := flag.Int("expected-status", 200, "expected HTTP status")
	expectedBody := flag.String("expected-body", "", "expected exact response body")
	expectedJSON := flag.String("expected-json", "", "expected JSON fields")
	requestBody := flag.String("body", "", "request body")
	contentType := flag.String("content-type", "application/json", "request content type")
	flag.Parse()

	if *url == "" {
		fmt.Fprintln(os.Stderr, "--url is required")
		os.Exit(2)
	}

	expectedFields := map[string]any{}
	if *expectedJSON != "" {
		if err := json.Unmarshal([]byte(*expectedJSON), &expectedFields); err != nil {
			fmt.Fprintf(os.Stderr, "invalid --expected-json: %v\n", err)
			os.Exit(2)
		}
	}

	client := &http.Client{
		Transport: &http.Transport{
			MaxIdleConns:        *concurrency * 4,
			MaxIdleConnsPerHost: *concurrency * 4,
			IdleConnTimeout:     90 * time.Second,
			TLSClientConfig:     &tls.Config{MinVersion: tls.VersionTLS12},
		},
		Timeout: 10 * time.Second,
	}

	if *warmup > 0 {
		_ = run(client, *url, *method, *concurrency, *warmup, *expectedStatus, *expectedBody, expectedFields, *requestBody, *contentType, false)
	}
	measured := run(client, *url, *method, *concurrency, *duration, *expectedStatus, *expectedBody, expectedFields, *requestBody, *contentType, true)
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

func run(client *http.Client, url string, method string, concurrency int, duration time.Duration, expectedStatus int, expectedBody string, expectedFields map[string]any, requestBody string, contentType string, collect bool) result {
	ctx, cancel := context.WithTimeout(context.Background(), duration)
	defer cancel()

	var requests int64
	var failures int64
	var responseBytes int64
	latencies := make([]int64, 0, 1024)
	var latencyLock sync.Mutex
	var wg sync.WaitGroup
	started := time.Now()

	for worker := 0; worker < concurrency; worker++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			localLatencies := make([]int64, 0, 1024)
			for ctx.Err() == nil {
				start := time.Now()
				body, err := request(client, url, method, requestBody, contentType, expectedStatus, expectedBody, expectedFields)
				elapsed := time.Since(start).Nanoseconds()
				if err != nil {
					atomic.AddInt64(&failures, 1)
					continue
				}
				atomic.AddInt64(&requests, 1)
				atomic.AddInt64(&responseBytes, int64(len(body)))
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
		URL:                         url,
		Method:                      method,
		Concurrency:                 concurrency,
		DurationSeconds:             elapsedSeconds,
		Requests:                    requests,
		Failures:                    failures,
		ResponseBytes:               responseBytes,
		ThroughputRequestsPerSecond: float64(requests) / elapsedSeconds,
		LatencyNs:                   summarizeLatency(latencies),
	}
}

func request(client *http.Client, url string, method string, requestBody string, contentType string, expectedStatus int, expectedBody string, expectedFields map[string]any) ([]byte, error) {
	var bodyReader io.Reader
	if requestBody != "" {
		bodyReader = strings.NewReader(requestBody)
	}
	req, err := http.NewRequest(method, url, bodyReader)
	if err != nil {
		return nil, err
	}
	if requestBody != "" {
		req.Header.Set("Content-Type", contentType)
	}
	resp, err := client.Do(req)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()

	body, err := io.ReadAll(resp.Body)
	if err != nil {
		return nil, err
	}
	if resp.StatusCode != expectedStatus {
		return nil, fmt.Errorf("expected HTTP %d, got %d", expectedStatus, resp.StatusCode)
	}
	if expectedBody != "" && string(body) != expectedBody {
		return nil, fmt.Errorf("unexpected response body")
	}
	if len(expectedFields) > 0 {
		actual := map[string]any{}
		if err := json.Unmarshal(body, &actual); err != nil {
			return nil, err
		}
		for key, expected := range expectedFields {
			if actual[key] != expected {
				return nil, fmt.Errorf("unexpected JSON field %s", key)
			}
		}
	}
	return body, nil
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
