FROM golang:1.26 AS build
WORKDIR /src
COPY benchmarks/go/go.mod benchmarks/go/
COPY benchmarks/go benchmarks/go
RUN go -C benchmarks/go build -o /app/perfapi ./cmd/api

FROM gcr.io/distroless/base-debian12
COPY --from=build /app/perfapi /perfapi
EXPOSE 8080
ENTRYPOINT ["/perfapi", "--port", "8080"]

