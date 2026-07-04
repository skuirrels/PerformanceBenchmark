FROM golang:1.26 AS build
WORKDIR /src
COPY benchmarks/go/go.mod benchmarks/go/
COPY benchmarks/go benchmarks/go
RUN go -C benchmarks/go build -o /app/perfapi ./cmd/api
RUN go -C benchmarks/go build -o /app/perfgrpc ./cmd/grpcapi

FROM gcr.io/distroless/base-debian12
COPY --from=build /app/perfapi /perfapi
COPY --from=build /app/perfgrpc /perfgrpc
EXPOSE 8080
ENTRYPOINT ["/perfapi", "--port", "8080"]
