# Website App

The website should be added after benchmark normalization is working.

Current interim output is a standalone generated report:

```bash
make report-latest
```

This writes:

```text
results/reports/<run-id>.html
```

Recommended shape:

- static-first Astro or Next.js export;
- charts driven by `website/data/*.json`;
- workload filters by category, profile, size, platform, and runtime version;
- methodology pages generated from `docs/`;
- raw artifact links for auditability.
