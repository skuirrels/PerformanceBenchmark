package main

import (
	"bufio"
	"bytes"
	"context"
	"encoding/binary"
	"encoding/json"
	"flag"
	"fmt"
	"io"
	"log"
	"net"
	"net/http"
	"os"
	"strconv"
	"strings"
	"sync"
	"sync/atomic"
	"time"

	"github.com/jackc/pgx/v5/pgxpool"
)

type apiPayload struct {
	Message string `json:"message"`
	Value   int    `json:"value"`
	Active  bool   `json:"active"`
}

type quoteRequest struct {
	CustomerID string  `json:"customerId"`
	ItemCount  int     `json:"itemCount"`
	UnitPrice  float64 `json:"unitPrice"`
	Expedited  bool    `json:"expedited"`
}

type quoteResponse struct {
	CustomerID string  `json:"customerId"`
	Total      float64 `json:"total"`
	Expedited  bool    `json:"expedited"`
	Accepted   bool    `json:"accepted"`
}

type fanoutResponse struct {
	Services int  `json:"services"`
	Bytes    int  `json:"bytes"`
	Complete bool `json:"complete"`
}

type formatPayload struct {
	ID       int     `json:"id"`
	Category string  `json:"category"`
	Amount   float64 `json:"amount"`
	Active   bool    `json:"active"`
}

type dbOrderResponse struct {
	ID         int    `json:"id"`
	CustomerID string `json:"customerId"`
	TotalCents int    `json:"totalCents"`
	Status     string `json:"status"`
}

type dbOrderPageResponse struct {
	CustomerID string            `json:"customerId"`
	Count      int               `json:"count"`
	Orders     []dbOrderResponse `json:"orders"`
}

type dbOrderWriteRequest struct {
	CustomerID string `json:"customerId"`
	TotalCents int    `json:"totalCents"`
	Status     string `json:"status"`
}

type dbOrderWriteResponse struct {
	ID       int64 `json:"id"`
	Accepted bool  `json:"accepted"`
}

func main() {
	port := flag.Int("port", 8080, "HTTP port")
	flag.Parse()

	payload := apiPayload{Message: "hello, world", Value: 42, Active: true}
	jsonPayload := []byte(`{"message":"hello, world","value":42,"active":true}`)
	downstreamPayload := []byte(`{"service":"downstream","value":42}`)
	client := &http.Client{
		Transport: &http.Transport{
			MaxIdleConns:        1024,
			MaxIdleConnsPerHost: 1024,
			MaxConnsPerHost:     1024,
			IdleConnTimeout:     90 * time.Second,
		},
		Timeout: 10 * time.Second,
	}
	var dbPool *pgxpool.Pool
	if connectionString := os.Getenv("PERFBENCH_DB"); connectionString != "" {
		pool, err := pgxpool.New(context.Background(), connectionString)
		if err != nil {
			log.Fatalf("failed to create db pool: %v", err)
		}
		defer pool.Close()
		dbPool = pool
	}
	var redisPool *redisConnectionPool
	if endpoint := os.Getenv("PERFBENCH_REDIS"); endpoint != "" {
		pool, err := newRedisConnectionPool(endpoint, 64)
		if err != nil {
			log.Fatalf("failed to create redis pool: %v", err)
		}
		redisPool = pool
	}

	mux := http.NewServeMux()
	mux.HandleFunc("/health", func(w http.ResponseWriter, _ *http.Request) {
		w.Header().Set("Content-Type", "text/plain")
		_, _ = w.Write([]byte("ok"))
	})
	mux.HandleFunc("/plaintext", func(w http.ResponseWriter, _ *http.Request) {
		w.Header().Set("Content-Type", "text/plain")
		_, _ = w.Write([]byte("hello, world"))
	})
	mux.HandleFunc("/json", func(w http.ResponseWriter, _ *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write(jsonPayload)
	})
	mux.HandleFunc("/json-serde", func(w http.ResponseWriter, _ *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		_ = json.NewEncoder(w).Encode(payload)
	})
	mux.HandleFunc("/orders/quote", func(w http.ResponseWriter, r *http.Request) {
		var request quoteRequest
		if err := json.NewDecoder(r.Body).Decode(&request); err != nil {
			http.Error(w, "bad request", http.StatusBadRequest)
			return
		}
		multiplier := 1.0
		if request.Expedited {
			multiplier = 1.2
		}
		writeJSON(w, quoteResponse{
			CustomerID: request.CustomerID,
			Total:      float64(int(request.UnitPrice*float64(request.ItemCount)*multiplier*100+0.5)) / 100,
			Expedited:  request.Expedited,
			Accepted:   true,
		})
	})
	mux.HandleFunc("/fanout", func(w http.ResponseWriter, r *http.Request) {
		baseURL := "http://" + r.Host
		paths := []string{"/downstream/a", "/downstream/b", "/downstream/c"}
		var wg sync.WaitGroup
		var totalBytes atomic.Int64
		var failures atomic.Int64
		for _, path := range paths {
			wg.Add(1)
			go func() {
				defer wg.Done()
				resp, err := client.Get(baseURL + path)
				if err != nil {
					failures.Add(1)
					return
				}
				defer resp.Body.Close()
				body, err := io.ReadAll(resp.Body)
				if err != nil || resp.StatusCode != http.StatusOK {
					failures.Add(1)
					return
				}
				totalBytes.Add(int64(len(body)))
			}()
		}
		wg.Wait()
		writeJSON(w, fanoutResponse{Services: len(paths), Bytes: int(totalBytes.Load()), Complete: failures.Load() == 0})
	})
	mux.HandleFunc("/downstream/", func(w http.ResponseWriter, _ *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write(downstreamPayload)
	})
	mux.HandleFunc("/serialize/json", func(w http.ResponseWriter, _ *http.Request) {
		writeJSON(w, formatPayload{ID: 123456, Category: "standard", Amount: 99.95, Active: true})
	})
	mux.HandleFunc("/serialize/binary", func(w http.ResponseWriter, _ *http.Request) {
		w.Header().Set("Content-Type", "application/octet-stream")
		_, _ = w.Write(encodeBinaryPayload(123456, "standard", 99.95, true))
	})
	mux.HandleFunc("/db/orders", func(w http.ResponseWriter, r *http.Request) {
		if dbPool == nil {
			http.Error(w, "database not configured", http.StatusServiceUnavailable)
			return
		}
		if r.Method == http.MethodPost {
			var request dbOrderWriteRequest
			if err := json.NewDecoder(r.Body).Decode(&request); err != nil {
				http.Error(w, "bad request", http.StatusBadRequest)
				return
			}
			var id int64
			err := dbPool.QueryRow(r.Context(),
				"insert into order_writes (customer_id, total_cents, status) values ($1, $2, $3) returning id",
				request.CustomerID, request.TotalCents, request.Status).Scan(&id)
			if err != nil {
				http.Error(w, "write failed", http.StatusInternalServerError)
				return
			}
			writeJSON(w, dbOrderWriteResponse{ID: id, Accepted: true})
			return
		}
		if r.Method != http.MethodGet {
			http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
			return
		}
		customerID := r.URL.Query().Get("customerId")
		if customerID == "" {
			customerID = "customer-42"
		}
		limit := 50
		if limitText := r.URL.Query().Get("limit"); limitText != "" {
			parsed, err := strconv.Atoi(limitText)
			if err == nil && parsed > 0 {
				limit = parsed
			}
		}
		if limit > 100 {
			limit = 100
		}
		rows, err := dbPool.Query(r.Context(), "select id, customer_id, total_cents, status from orders where customer_id = $1 order by id limit $2", customerID, limit)
		if err != nil {
			http.Error(w, "query failed", http.StatusInternalServerError)
			return
		}
		defer rows.Close()
		orders := make([]dbOrderResponse, 0, limit)
		for rows.Next() {
			var order dbOrderResponse
			if err := rows.Scan(&order.ID, &order.CustomerID, &order.TotalCents, &order.Status); err != nil {
				http.Error(w, "scan failed", http.StatusInternalServerError)
				return
			}
			orders = append(orders, order)
		}
		writeJSON(w, dbOrderPageResponse{CustomerID: customerID, Count: len(orders), Orders: orders})
	})
	mux.HandleFunc("/db/orders/", func(w http.ResponseWriter, r *http.Request) {
		if dbPool == nil {
			http.Error(w, "database not configured", http.StatusServiceUnavailable)
			return
		}
		idText := r.URL.Path[len("/db/orders/"):]
		id, err := strconv.Atoi(idText)
		if err != nil {
			http.Error(w, "bad id", http.StatusBadRequest)
			return
		}
		var response dbOrderResponse
		err = dbPool.QueryRow(r.Context(), "select id, customer_id, total_cents, status from orders where id = $1", id).
			Scan(&response.ID, &response.CustomerID, &response.TotalCents, &response.Status)
		if err != nil {
			http.Error(w, "not found", http.StatusNotFound)
			return
		}
		writeJSON(w, response)
	})
	mux.HandleFunc("/cache/orders/", func(w http.ResponseWriter, r *http.Request) {
		if redisPool == nil {
			http.Error(w, "redis not configured", http.StatusServiceUnavailable)
			return
		}
		idText := r.URL.Path[len("/cache/orders/"):]
		id, err := strconv.Atoi(idText)
		if err != nil {
			http.Error(w, "bad id", http.StatusBadRequest)
			return
		}
		key := "order:" + idText
		if cached, ok, err := redisPool.get(key); err == nil && ok {
			w.Header().Set("Content-Type", "application/json")
			_, _ = w.Write(cached)
			return
		}
		if dbPool == nil {
			http.Error(w, "not found", http.StatusNotFound)
			return
		}
		var response dbOrderResponse
		err = dbPool.QueryRow(r.Context(), "select id, customer_id, total_cents, status from orders where id = $1", id).
			Scan(&response.ID, &response.CustomerID, &response.TotalCents, &response.Status)
		if err != nil {
			http.Error(w, "not found", http.StatusNotFound)
			return
		}
		body, _ := json.Marshal(response)
		_ = redisPool.set(key, body)
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write(body)
	})

	server := &http.Server{
		Addr:              ":" + strconv.Itoa(*port),
		Handler:           mux,
		ReadHeaderTimeout: 5 * time.Second,
	}

	log.Printf("listening http://0.0.0.0:%d", *port)
	log.Fatal(server.ListenAndServe())
}

func writeJSON(w http.ResponseWriter, value any) {
	w.Header().Set("Content-Type", "application/json")
	_ = json.NewEncoder(w).Encode(value)
}

func encodeBinaryPayload(id int32, category string, amount float64, active bool) []byte {
	buffer := bytes.NewBuffer(make([]byte, 0, 32+len(category)))
	_ = binary.Write(buffer, binary.LittleEndian, id)
	_ = binary.Write(buffer, binary.LittleEndian, uint16(len(category)))
	buffer.WriteString(category)
	_ = binary.Write(buffer, binary.LittleEndian, amount)
	if active {
		buffer.WriteByte(1)
	} else {
		buffer.WriteByte(0)
	}
	return buffer.Bytes()
}

type redisConnectionPool struct {
	connections chan *redisConnection
}

func newRedisConnectionPool(endpoint string, size int) (*redisConnectionPool, error) {
	pool := &redisConnectionPool{connections: make(chan *redisConnection, size)}
	for i := 0; i < size; i++ {
		connection, err := dialRedis(endpoint)
		if err != nil {
			return nil, err
		}
		pool.connections <- connection
	}
	return pool, nil
}

func (p *redisConnectionPool) get(key string) ([]byte, bool, error) {
	connection := <-p.connections
	defer func() { p.connections <- connection }()
	return connection.get(key)
}

func (p *redisConnectionPool) set(key string, value []byte) error {
	connection := <-p.connections
	defer func() { p.connections <- connection }()
	return connection.set(key, value)
}

type redisConnection struct {
	conn   net.Conn
	reader *bufio.Reader
}

func dialRedis(endpoint string) (*redisConnection, error) {
	if !strings.Contains(endpoint, ":") {
		endpoint += ":6379"
	}
	conn, err := net.DialTimeout("tcp", endpoint, 2*time.Second)
	if err != nil {
		return nil, err
	}
	return &redisConnection{conn: conn, reader: bufio.NewReader(conn)}, nil
}

func (c *redisConnection) get(key string) ([]byte, bool, error) {
	if _, err := fmt.Fprintf(c.conn, "*2\r\n$3\r\nGET\r\n$%d\r\n%s\r\n", len(key), key); err != nil {
		return nil, false, err
	}
	return c.readBulk()
}

func (c *redisConnection) set(key string, value []byte) error {
	if _, err := fmt.Fprintf(c.conn, "*3\r\n$3\r\nSET\r\n$%d\r\n%s\r\n$%d\r\n%s\r\n", len(key), key, len(value), value); err != nil {
		return err
	}
	line, err := c.reader.ReadString('\n')
	if err != nil {
		return err
	}
	if !strings.HasPrefix(line, "+OK") {
		return fmt.Errorf("unexpected redis response: %s", strings.TrimSpace(line))
	}
	return nil
}

func (c *redisConnection) readBulk() ([]byte, bool, error) {
	line, err := c.reader.ReadString('\n')
	if err != nil {
		return nil, false, err
	}
	if !strings.HasPrefix(line, "$") {
		return nil, false, fmt.Errorf("unexpected redis response: %s", strings.TrimSpace(line))
	}
	length, err := strconv.Atoi(strings.TrimSpace(line[1:]))
	if err != nil {
		return nil, false, err
	}
	if length < 0 {
		return nil, false, nil
	}
	body := make([]byte, length+2)
	if _, err := io.ReadFull(c.reader, body); err != nil {
		return nil, false, err
	}
	return body[:length], true, nil
}
