package main

import (
	"context"
	"flag"
	"fmt"
	"log"
	"math"
	"net"

	"dev/perfbench/benchmarks/go/internal/perfpb"
	"google.golang.org/grpc"
)

type quoteServer struct {
	perfpb.UnimplementedQuoteServiceServer
}

func main() {
	port := flag.Int("port", 8080, "gRPC port")
	flag.Parse()

	listener, err := net.Listen("tcp", fmt.Sprintf("0.0.0.0:%d", *port))
	if err != nil {
		log.Fatalf("failed to listen: %v", err)
	}

	server := grpc.NewServer()
	perfpb.RegisterQuoteServiceServer(server, quoteServer{})
	log.Printf("listening grpc://0.0.0.0:%d", *port)
	if err := server.Serve(listener); err != nil {
		log.Fatalf("server failed: %v", err)
	}
}

func (quoteServer) Quote(_ context.Context, request *perfpb.QuoteRequest) (*perfpb.QuoteResponse, error) {
	multiplier := 1.0
	if request.Expedited {
		multiplier = 1.2
	}
	total := math.Round(float64(request.ItemCount)*request.UnitPrice*multiplier*100) / 100
	return &perfpb.QuoteResponse{
		CustomerId: request.CustomerId,
		Total:      total,
		Expedited:  request.Expedited,
		Accepted:   true,
	}, nil
}
