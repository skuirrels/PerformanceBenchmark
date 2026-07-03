# Linux Web Runs

Most public web benchmark results are produced on Linux. The local macOS web
profile is useful for development, but Linux runs are the better publication
target.

Quick smoke run:

```bash
make compare-web-docker-smoke
```

Full Docker-backed web comparison:

```bash
make compare-web-docker RUN_ID=linux-web-001
```

The Make target builds these images before running:

```bash
docker build -f docker/web/Dockerfile.dotnet -t perfapi-dotnet .
docker build -f docker/web/Dockerfile.dotnet-pgo -t perfapi-dotnet-pgo .
docker build -f docker/web/Dockerfile.java -t perfapi-java .
docker build -f docker/web/Dockerfile.java-virtual -t perfapi-java-virtual .
docker build -f docker/web/Dockerfile.java-vertx -t perfapi-java-vertx .
docker build -f docker/web/Dockerfile.go -t perfapi-go .
```

The Docker runner starts one container per benchmark lane, publishes container
port `8080` to a random localhost port, waits for `/health`, drives load with
the compiled Go load generator, then stops the container. Results are normalized
to the same `results/normalized/<run-id>.json` shape as host runs.
