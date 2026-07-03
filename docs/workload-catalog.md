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
| `startup.cli` | Startup | process launch and first useful output |

## Implementation Rules

- Workloads must be named identically across languages.
- Dataset generation must use the same seed and documented algorithm.
- A workload can have multiple lanes, such as standard library and popular
  library, but lanes must not be collapsed into one result.
- Any unsafe, native, SIMD, or preview feature must be labeled as an optimization
  lane.

