# Website Plan

The website should explain the benchmark results without hiding methodology.

## Primary Views

1. Overview: selected headline charts and key caveats.
2. Workloads: filterable benchmark catalog with per-workload charts.
3. Platforms: runtime versions, flags, and implementation notes.
4. Runs: historical run list with machine metadata.
5. Methodology: benchmark rules, limitations, and reproducibility commands.
6. Raw Data: downloadable normalized JSON and native harness output.

## Chart Requirements

- Show confidence intervals where available.
- Avoid single universal rankings.
- Group by workload category.
- Let readers switch units between time/op and ops/sec.
- Call out local machine, OS, architecture, and runtime versions near every
  published result set.

## Data Contract

The website consumes:

```text
website/data/latest.json
website/data/runs/<run-id>.json
website/data/runs/index.json
```

These files should be copied from `results/normalized/` by the release pipeline.

## Stack Recommendation

Use a static-first site after benchmark normalization is stable:

- Astro or Next.js static export for the public website.
- Lightweight client-side filtering for charts.
- No server-side benchmark execution from the website.

The benchmark runner remains separate from the website.

