# Website Data

This directory is the future public data surface.

The release process should copy normalized artifacts here:

```text
latest.json
runs/index.json
runs/<run-id>.json
```

Do not commit raw harness output into the website app. Keep raw output under
`results/raw/` and link to it from normalized result metadata.

