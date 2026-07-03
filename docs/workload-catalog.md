# Workload Catalog

The suite should grow in layers. Start with small deterministic workloads, then
add more realistic end-to-end profiles.

## Initial Micro Workloads

| ID | Category | Measures | Notes |
| --- | --- | --- | --- |
| `cpu.nbody` | CPU numeric | floating-point loops, object layout | Deterministic solar-system simulation |
| `data.json-serde` | Data | parser and serializer behavior | Standard-library first, external library lanes later |
| `collections.hash-map` | Collections | hashing, lookup, allocation | Fixed key set with deterministic lookup order |
| `crypto.sha256` | CPU/hash | library intrinsic throughput | Same byte buffers across platforms |
| `text.regex` | Text | regex engine behavior | Separate compile and match profiles |
| `data.sort` | Collections | sort implementation and comparer overhead | Fixed pseudo-random dataset |

## Next Workloads

| ID | Category | Measures |
| --- | --- | --- |
| `memory.object-graph` | Memory | allocation rate, retained heap, GC behavior |
| `concurrency.worker-pool` | Concurrency | scheduler overhead and queue throughput |
| `io.file-stream` | IO | buffered file read/write throughput |
| `http.plaintext` | Web | baseline HTTP request throughput |
| `http.json` | Web | static JSON response throughput and latency |
| `http.json-serde` | Web | per-request JSON serialization throughput and latency |
| `http.quote` | Web | JSON request body parse, calculation, and JSON response |
| `http.fanout` | Web | HTTP client fan-out, scheduler behavior, aggregation |
| `format.json` | Serialization | JSON wire-format response generation |
| `format.binary` | Serialization | compact binary wire-format response generation |
| `http.db-lookup` | Web/DB | indexed lookup, connection pooling, JSON projection |
| `http.db-page` | Web/DB | filtered paged query returning 50 JSON rows |
| `http.db-write` | Web/DB | transaction throughput and generated-key response |
| `http.cache-hit` | Cache | Redis hot-key read, HTTP response projection |
| `grpc.quote` | RPC | planned gRPC/protobuf unary quote request |
| `startup.cli` | Startup | process launch and first useful output |

## Implementation Rules

- Workloads must be named identically across languages.
- Dataset generation must use the same seed and documented algorithm.
- A workload can have multiple lanes, such as standard library and popular
  library, but lanes must not be collapsed into one result.
- Any unsafe, native, SIMD, or preview feature must be labeled as an optimization
  lane.
