PYTHON ?= python3
BENCHCTL := $(PYTHON) tools/benchctl/benchctl.py
RUN_ID ?= local-$(shell date -u +%Y%m%dT%H%M%SZ)
GOCACHE ?= $(CURDIR)/.cache/go-build
MAVEN_REPO ?= $(CURDIR)/.cache/m2
REPEAT ?= 1
DB_PORT_ORIGIN := $(origin DB_PORT)
DB_PORT_INPUT := $(value DB_PORT)
REDIS_PORT_ORIGIN := $(origin REDIS_PORT)
REDIS_PORT_INPUT := $(value REDIS_PORT)
DB_PORT ?= 55432
REDIS_PORT ?= 56379
GIST_VISIBILITY ?= public
DOCKER_NETWORK ?= perfbench-net
COMPARE_ALL_DOCKER_DB_PORT ?= 56543
COMPARE_ALL_DOCKER_REDIS_PORT ?= 56380
DOTNET_TFB_VALIDATE_BENCHMARKS ?= http.plaintext http.json-serde http.quote http.fanout
DOTNET_TFB_VALIDATE_DURATION ?= 45
DOTNET_TFB_VALIDATE_WARMUP ?= 15
DOTNET_TFB_VALIDATE_CONCURRENCY ?= 16,64
LAST_RUN_FILE := .cache/last-run-id

.PHONY: help validate plan env smoke compare-smoke compare-all-smoke web-smoke compare-web-smoke web compare-web grpc-smoke compare-grpc-smoke grpc compare-grpc db-up db-seed db-down db-smoke compare-db-smoke db compare-db redis-up redis-seed redis-down cache-smoke compare-cache-smoke cache compare-cache docker-web-build dotnet-tfb-validate web-docker-smoke compare-web-docker-smoke web-docker compare-web-docker compare-all-docker-smoke compare-all-docker full compare compare-all run benchmark normalize summarize resources-latest report-latest publish-report-gist compare-latest smoke-dotnet smoke-java smoke-go run-dotnet run-java run-go

help:
	@echo "Common commands:"
	@echo "  make smoke          Run all platforms in smoke mode, normalize, summarize"
	@echo "  make compare-smoke  Run all platforms in smoke mode, normalize, compare"
	@echo "  make compare-all-smoke Start Postgres, run every benchmark in smoke mode, compare, report"
	@echo "  make web-smoke      Run web API benchmarks in smoke mode"
	@echo "  make compare-web-smoke Run web API smoke benchmarks and compare"
	@echo "  make compare-grpc-smoke Run gRPC API smoke benchmarks and compare"
	@echo "  make compare-grpc   Run full gRPC API benchmarks and compare"
	@echo "  make full           Run full benchmarks, normalize, summarize"
	@echo "  make compare        Run full benchmarks, normalize, compare"
	@echo "  make compare-all    Start Postgres, run every benchmark, compare, report"
	@echo "  make web            Run full web API benchmarks"
	@echo "  make compare-web    Run full web API benchmarks and compare"
	@echo "  make compare-db-smoke Start Postgres, run DB API smoke benchmarks, compare"
	@echo "  make compare-db     Start Postgres, run full DB API benchmarks, compare"
	@echo "  make compare-cache-smoke Start Redis, run cache API smoke benchmarks, compare"
	@echo "  make compare-cache  Start Redis, run full cache API benchmarks, compare"
	@echo "  make docker-web-build Build Linux web API Docker images"
	@echo "  make dotnet-tfb-validate Run an isolated ~10 minute Docker validation for the .NET TFB-style lane"
	@echo "  make compare-web-docker-smoke Build Docker images, run web smoke benchmarks, compare"
	@echo "  make compare-web-docker Build Docker images, run full web benchmarks, compare"
	@echo "  make compare-all-docker-smoke Build Docker images, run full suite in Docker smoke mode, compare, report"
	@echo "  make compare-all-docker Build Docker images, run full suite with Docker-backed API lanes, compare, report"
	@echo "                          Defaults: DB_PORT=$(COMPARE_ALL_DOCKER_DB_PORT), REDIS_PORT=$(COMPARE_ALL_DOCKER_REDIS_PORT)"
	@echo "  make smoke-java     Run Java smoke benchmarks only"
	@echo "  make smoke-dotnet   Run .NET smoke benchmarks only"
	@echo "  make smoke-go       Run Go smoke benchmarks only"
	@echo "  make normalize      Normalize the most recent make-run"
	@echo "  make summarize      Summarize the most recent normalized run"
	@echo "  make resources-latest Print resource samples for the most recent normalized run"
	@echo "  make report-latest Generate an HTML report for the most recent normalized run"
	@echo "  make publish-report-gist Publish an HTML report as a Gist and print a rendered preview URL"
	@echo "  make compare-latest Compare the most recent normalized run"
	@echo ""
	@echo "Optional:"
	@echo "  make smoke RUN_ID=my-run"
	@echo "  make compare-web-docker REPEAT=3"

validate:
	$(BENCHCTL) validate

plan:
	$(BENCHCTL) plan

env:
	$(BENCHCTL) env

smoke: db-seed redis-seed
	@mkdir -p .cache
	@echo "$(RUN_ID)" > $(LAST_RUN_FILE)
	@set +e; \
	PERFBENCH_DB_PORT=$(DB_PORT) PERFBENCH_REDIS_PORT=$(REDIS_PORT) $(BENCHCTL) run --smoke --run-id $(RUN_ID); \
	status=$$?; \
	$(BENCHCTL) normalize $(RUN_ID); \
	$(BENCHCTL) summarize results/normalized/$(RUN_ID).json; \
	$(BENCHCTL) report results/normalized/$(RUN_ID).json; \
	echo "make $@ finished at $$(date -u +%Y-%m-%dT%H:%M:%SZ)"; \
	exit $$status

compare-smoke compare-all-smoke: db-seed redis-seed
	@mkdir -p .cache
	@echo "$(RUN_ID)" > $(LAST_RUN_FILE)
	@set +e; \
	PERFBENCH_DB_PORT=$(DB_PORT) PERFBENCH_REDIS_PORT=$(REDIS_PORT) $(BENCHCTL) run --smoke --run-id $(RUN_ID); \
	status=$$?; \
	$(BENCHCTL) normalize $(RUN_ID); \
	$(BENCHCTL) compare --allow-missing results/normalized/$(RUN_ID).json; \
	$(BENCHCTL) report results/normalized/$(RUN_ID).json; \
	echo "make $@ finished at $$(date -u +%Y-%m-%dT%H:%M:%SZ)"; \
	exit $$status

web-smoke:
	@mkdir -p .cache
	@echo "$(RUN_ID)" > $(LAST_RUN_FILE)
	@set +e; \
	$(BENCHCTL) run --profile web-api --smoke --repeat $(REPEAT) --run-id $(RUN_ID); \
	status=$$?; \
	$(BENCHCTL) normalize $(RUN_ID); \
	$(BENCHCTL) summarize results/normalized/$(RUN_ID).json; \
	exit $$status

compare-web-smoke:
	@mkdir -p .cache
	@echo "$(RUN_ID)" > $(LAST_RUN_FILE)
	@set +e; \
	$(BENCHCTL) run --profile web-api --smoke --repeat $(REPEAT) --run-id $(RUN_ID); \
	status=$$?; \
	$(BENCHCTL) normalize $(RUN_ID); \
	$(BENCHCTL) compare --allow-missing results/normalized/$(RUN_ID).json; \
	exit $$status

full run benchmark: db-seed redis-seed
	@mkdir -p .cache
	@echo "$(RUN_ID)" > $(LAST_RUN_FILE)
	@set +e; \
	PERFBENCH_DB_PORT=$(DB_PORT) PERFBENCH_REDIS_PORT=$(REDIS_PORT) $(BENCHCTL) run --run-id $(RUN_ID); \
	status=$$?; \
	$(BENCHCTL) normalize $(RUN_ID); \
	$(BENCHCTL) summarize results/normalized/$(RUN_ID).json; \
	$(BENCHCTL) report results/normalized/$(RUN_ID).json; \
	echo "make $@ finished at $$(date -u +%Y-%m-%dT%H:%M:%SZ)"; \
	exit $$status

web:
	@mkdir -p .cache
	@echo "$(RUN_ID)" > $(LAST_RUN_FILE)
	@set +e; \
	$(BENCHCTL) run --profile web-api --repeat $(REPEAT) --run-id $(RUN_ID); \
	status=$$?; \
	$(BENCHCTL) normalize $(RUN_ID); \
	$(BENCHCTL) summarize results/normalized/$(RUN_ID).json; \
	exit $$status

compare compare-all: db-seed redis-seed
	@mkdir -p .cache
	@echo "$(RUN_ID)" > $(LAST_RUN_FILE)
	@set +e; \
	PERFBENCH_DB_PORT=$(DB_PORT) PERFBENCH_REDIS_PORT=$(REDIS_PORT) $(BENCHCTL) run --run-id $(RUN_ID); \
	status=$$?; \
	$(BENCHCTL) normalize $(RUN_ID); \
	$(BENCHCTL) compare --allow-missing results/normalized/$(RUN_ID).json; \
	$(BENCHCTL) report results/normalized/$(RUN_ID).json; \
	echo "make $@ finished at $$(date -u +%Y-%m-%dT%H:%M:%SZ)"; \
	exit $$status

compare-web:
	@mkdir -p .cache
	@echo "$(RUN_ID)" > $(LAST_RUN_FILE)
	@set +e; \
	$(BENCHCTL) run --profile web-api --repeat $(REPEAT) --run-id $(RUN_ID); \
	status=$$?; \
	$(BENCHCTL) normalize $(RUN_ID); \
	$(BENCHCTL) compare --allow-missing results/normalized/$(RUN_ID).json; \
	exit $$status

grpc-smoke:
	@mkdir -p .cache
	@echo "$(RUN_ID)" > $(LAST_RUN_FILE)
	@set +e; \
	$(BENCHCTL) run --profile grpc-api --smoke --repeat $(REPEAT) --run-id $(RUN_ID); \
	status=$$?; \
	$(BENCHCTL) normalize $(RUN_ID); \
	$(BENCHCTL) summarize results/normalized/$(RUN_ID).json; \
	exit $$status

compare-grpc-smoke:
	@mkdir -p .cache
	@echo "$(RUN_ID)" > $(LAST_RUN_FILE)
	@set +e; \
	$(BENCHCTL) run --profile grpc-api --smoke --repeat $(REPEAT) --run-id $(RUN_ID); \
	status=$$?; \
	$(BENCHCTL) normalize $(RUN_ID); \
	$(BENCHCTL) compare --allow-missing results/normalized/$(RUN_ID).json; \
	exit $$status

grpc:
	@mkdir -p .cache
	@echo "$(RUN_ID)" > $(LAST_RUN_FILE)
	@set +e; \
	$(BENCHCTL) run --profile grpc-api --repeat $(REPEAT) --run-id $(RUN_ID); \
	status=$$?; \
	$(BENCHCTL) normalize $(RUN_ID); \
	$(BENCHCTL) summarize results/normalized/$(RUN_ID).json; \
	exit $$status

compare-grpc:
	@mkdir -p .cache
	@echo "$(RUN_ID)" > $(LAST_RUN_FILE)
	@set +e; \
	$(BENCHCTL) run --profile grpc-api --repeat $(REPEAT) --run-id $(RUN_ID); \
	status=$$?; \
	$(BENCHCTL) normalize $(RUN_ID); \
	$(BENCHCTL) compare --allow-missing results/normalized/$(RUN_ID).json; \
	exit $$status

db-up:
	-docker rm -f perfbench-postgres >/dev/null 2>&1
	@if command -v lsof >/dev/null 2>&1 && lsof -nP -iTCP:$(DB_PORT) -sTCP:LISTEN >/dev/null 2>&1; then \
		echo "ERROR: DB_PORT=$(DB_PORT) is already in use."; \
		lsof -nP -iTCP:$(DB_PORT) -sTCP:LISTEN; \
		echo "Retry with: make compare-all DB_PORT=56543"; \
		exit 1; \
	fi
	@docker network inspect $(DOCKER_NETWORK) >/dev/null 2>&1 || docker network create $(DOCKER_NETWORK) >/dev/null
	docker run --name perfbench-postgres --network $(DOCKER_NETWORK) -e POSTGRES_USER=perfbench -e POSTGRES_PASSWORD=perfbench -e POSTGRES_DB=perfbench -p 127.0.0.1:$(DB_PORT):5432 -d postgres:17-alpine
	@until docker exec -e PGPASSWORD=perfbench perfbench-postgres psql -h 127.0.0.1 -U perfbench -d perfbench -c 'select 1' >/dev/null 2>&1; do sleep 1; done

db-seed: db-up
	docker exec -i -e PGPASSWORD=perfbench perfbench-postgres psql -h 127.0.0.1 -U perfbench -d perfbench < docker/postgres/schema.sql

db-down:
	docker rm -f perfbench-postgres

redis-up:
	-docker rm -f perfbench-redis >/dev/null 2>&1
	@if command -v lsof >/dev/null 2>&1 && lsof -nP -iTCP:$(REDIS_PORT) -sTCP:LISTEN >/dev/null 2>&1; then \
		echo "ERROR: REDIS_PORT=$(REDIS_PORT) is already in use."; \
		lsof -nP -iTCP:$(REDIS_PORT) -sTCP:LISTEN; \
		echo "Retry with: make compare-all REDIS_PORT=56380"; \
		exit 1; \
	fi
	@docker network inspect $(DOCKER_NETWORK) >/dev/null 2>&1 || docker network create $(DOCKER_NETWORK) >/dev/null
	docker run --name perfbench-redis --network $(DOCKER_NETWORK) -p 127.0.0.1:$(REDIS_PORT):6379 -d redis:8-alpine
	@until docker exec perfbench-redis redis-cli ping >/dev/null 2>&1; do sleep 1; done

redis-seed: redis-up
	docker exec perfbench-redis redis-cli set order:42 '{"id":42,"customerId":"customer-42","totalCents":1042,"status":"ready"}' >/dev/null

redis-down:
	docker rm -f perfbench-redis

db-smoke: db-seed
	@mkdir -p .cache
	@echo "$(RUN_ID)" > $(LAST_RUN_FILE)
	@set +e; \
	PERFBENCH_DB_PORT=$(DB_PORT) $(BENCHCTL) run --profile db-api --smoke --repeat $(REPEAT) --run-id $(RUN_ID); \
	status=$$?; \
	$(BENCHCTL) normalize $(RUN_ID); \
	$(BENCHCTL) summarize results/normalized/$(RUN_ID).json; \
	exit $$status

compare-db-smoke: db-seed
	@mkdir -p .cache
	@echo "$(RUN_ID)" > $(LAST_RUN_FILE)
	@set +e; \
	PERFBENCH_DB_PORT=$(DB_PORT) $(BENCHCTL) run --profile db-api --smoke --repeat $(REPEAT) --run-id $(RUN_ID); \
	status=$$?; \
	$(BENCHCTL) normalize $(RUN_ID); \
	$(BENCHCTL) compare --allow-missing results/normalized/$(RUN_ID).json; \
	exit $$status

db: db-seed
	@mkdir -p .cache
	@echo "$(RUN_ID)" > $(LAST_RUN_FILE)
	@set +e; \
	PERFBENCH_DB_PORT=$(DB_PORT) $(BENCHCTL) run --profile db-api --repeat $(REPEAT) --run-id $(RUN_ID); \
	status=$$?; \
	$(BENCHCTL) normalize $(RUN_ID); \
	$(BENCHCTL) summarize results/normalized/$(RUN_ID).json; \
	exit $$status

compare-db: db-seed
	@mkdir -p .cache
	@echo "$(RUN_ID)" > $(LAST_RUN_FILE)
	@set +e; \
	PERFBENCH_DB_PORT=$(DB_PORT) $(BENCHCTL) run --profile db-api --repeat $(REPEAT) --run-id $(RUN_ID); \
	status=$$?; \
	$(BENCHCTL) normalize $(RUN_ID); \
	$(BENCHCTL) compare --allow-missing results/normalized/$(RUN_ID).json; \
	exit $$status

cache-smoke: redis-seed
	@mkdir -p .cache
	@echo "$(RUN_ID)" > $(LAST_RUN_FILE)
	@set +e; \
	PERFBENCH_REDIS_PORT=$(REDIS_PORT) $(BENCHCTL) run --profile cache-api --smoke --repeat $(REPEAT) --run-id $(RUN_ID); \
	status=$$?; \
	$(BENCHCTL) normalize $(RUN_ID); \
	$(BENCHCTL) summarize results/normalized/$(RUN_ID).json; \
	exit $$status

compare-cache-smoke: redis-seed
	@mkdir -p .cache
	@echo "$(RUN_ID)" > $(LAST_RUN_FILE)
	@set +e; \
	PERFBENCH_REDIS_PORT=$(REDIS_PORT) $(BENCHCTL) run --profile cache-api --smoke --repeat $(REPEAT) --run-id $(RUN_ID); \
	status=$$?; \
	$(BENCHCTL) normalize $(RUN_ID); \
	$(BENCHCTL) compare --allow-missing results/normalized/$(RUN_ID).json; \
	exit $$status

cache: redis-seed
	@mkdir -p .cache
	@echo "$(RUN_ID)" > $(LAST_RUN_FILE)
	@set +e; \
	PERFBENCH_REDIS_PORT=$(REDIS_PORT) $(BENCHCTL) run --profile cache-api --repeat $(REPEAT) --run-id $(RUN_ID); \
	status=$$?; \
	$(BENCHCTL) normalize $(RUN_ID); \
	$(BENCHCTL) summarize results/normalized/$(RUN_ID).json; \
	exit $$status

compare-cache: redis-seed
	@mkdir -p .cache
	@echo "$(RUN_ID)" > $(LAST_RUN_FILE)
	@set +e; \
	PERFBENCH_REDIS_PORT=$(REDIS_PORT) $(BENCHCTL) run --profile cache-api --repeat $(REPEAT) --run-id $(RUN_ID); \
	status=$$?; \
	$(BENCHCTL) normalize $(RUN_ID); \
	$(BENCHCTL) compare --allow-missing results/normalized/$(RUN_ID).json; \
	exit $$status

docker-web-build:
	docker build -f docker/web/Dockerfile.dotnet -t perfapi-dotnet .
	docker build -f docker/web/Dockerfile.dotnet-pgo -t perfapi-dotnet-pgo .
	docker build -f docker/web/Dockerfile.dotnet-tuned -t perfapi-dotnet-tuned .
	docker build -f docker/web/Dockerfile.dotnet-tfb -t perfapi-dotnet-tfb .
	docker build -f docker/web/Dockerfile.java -t perfapi-java .
	docker build -f docker/web/Dockerfile.java-virtual -t perfapi-java-virtual .
	docker build -f docker/web/Dockerfile.java-spring -t perfapi-java-spring .
	docker build -f docker/web/Dockerfile.java-vertx -t perfapi-java-vertx .
	docker build -f docker/web/Dockerfile.go -t perfapi-go .

dotnet-tfb-validate:
	@echo "make dotnet-tfb-validate started at $$(date -u +%Y-%m-%dT%H:%M:%SZ)"
	docker build -f docker/web/Dockerfile.dotnet-tfb -t perfapi-dotnet-tfb .
	@mkdir -p .cache
	@echo "$(RUN_ID)" > $(LAST_RUN_FILE)
	@set +e; \
	status=0; \
	for benchmark in $(DOTNET_TFB_VALIDATE_BENCHMARKS); do \
		PERFBENCH_DURATION_SECONDS=$(DOTNET_TFB_VALIDATE_DURATION) \
		PERFBENCH_WARMUP_SECONDS=$(DOTNET_TFB_VALIDATE_WARMUP) \
		PERFBENCH_CONCURRENCY=$(DOTNET_TFB_VALIDATE_CONCURRENCY) \
		$(BENCHCTL) run --platform dotnet-tfb --benchmark $$benchmark --web-runner docker --repeat $(REPEAT) --run-id $(RUN_ID) || status=$$?; \
	done; \
	$(BENCHCTL) normalize $(RUN_ID); \
	$(BENCHCTL) summarize results/normalized/$(RUN_ID).json; \
	$(BENCHCTL) report results/normalized/$(RUN_ID).json; \
	echo "make $@ finished at $$(date -u +%Y-%m-%dT%H:%M:%SZ)"; \
	exit $$status

web-docker-smoke:
	@echo "make web-docker-smoke started at $$(date -u +%Y-%m-%dT%H:%M:%SZ)"
	$(MAKE) docker-web-build
	@mkdir -p .cache
	@echo "$(RUN_ID)" > $(LAST_RUN_FILE)
	@set +e; \
	$(BENCHCTL) run --profile web-api --smoke --web-runner docker --repeat $(REPEAT) --run-id $(RUN_ID); \
	status=$$?; \
	$(BENCHCTL) normalize $(RUN_ID); \
	$(BENCHCTL) summarize results/normalized/$(RUN_ID).json; \
	exit $$status

compare-web-docker-smoke:
	@echo "make compare-web-docker-smoke started at $$(date -u +%Y-%m-%dT%H:%M:%SZ)"
	$(MAKE) docker-web-build
	@mkdir -p .cache
	@echo "$(RUN_ID)" > $(LAST_RUN_FILE)
	@set +e; \
	$(BENCHCTL) run --profile web-api --smoke --web-runner docker --repeat $(REPEAT) --run-id $(RUN_ID); \
	status=$$?; \
	$(BENCHCTL) normalize $(RUN_ID); \
	$(BENCHCTL) compare --allow-missing results/normalized/$(RUN_ID).json; \
	exit $$status

web-docker:
	@echo "make web-docker started at $$(date -u +%Y-%m-%dT%H:%M:%SZ)"
	$(MAKE) docker-web-build
	@mkdir -p .cache
	@echo "$(RUN_ID)" > $(LAST_RUN_FILE)
	@set +e; \
	$(BENCHCTL) run --profile web-api --web-runner docker --repeat $(REPEAT) --run-id $(RUN_ID); \
	status=$$?; \
	$(BENCHCTL) normalize $(RUN_ID); \
	$(BENCHCTL) summarize results/normalized/$(RUN_ID).json; \
	exit $$status

compare-web-docker:
	@echo "make compare-web-docker started at $$(date -u +%Y-%m-%dT%H:%M:%SZ)"
	$(MAKE) docker-web-build
	@mkdir -p .cache
	@echo "$(RUN_ID)" > $(LAST_RUN_FILE)
	@set +e; \
	$(BENCHCTL) run --profile web-api --web-runner docker --repeat $(REPEAT) --run-id $(RUN_ID); \
	status=$$?; \
	$(BENCHCTL) normalize $(RUN_ID); \
	$(BENCHCTL) compare --allow-missing results/normalized/$(RUN_ID).json; \
	exit $$status

compare-all-docker-smoke: DB_PORT = $(if $(filter undefined,$(DB_PORT_ORIGIN)),$(COMPARE_ALL_DOCKER_DB_PORT),$(DB_PORT_INPUT))
compare-all-docker-smoke: REDIS_PORT = $(if $(filter undefined,$(REDIS_PORT_ORIGIN)),$(COMPARE_ALL_DOCKER_REDIS_PORT),$(REDIS_PORT_INPUT))
compare-all-docker-smoke: db-seed redis-seed
	@echo "make compare-all-docker-smoke started at $$(date -u +%Y-%m-%dT%H:%M:%SZ)"
	$(MAKE) docker-web-build
	@mkdir -p .cache
	@echo "$(RUN_ID)" > $(LAST_RUN_FILE)
	@set +e; \
	PERFBENCH_DB_PORT=$(DB_PORT) PERFBENCH_REDIS_PORT=$(REDIS_PORT) PERFBENCH_DOCKER_NETWORK=$(DOCKER_NETWORK) $(BENCHCTL) run --smoke --web-runner docker --repeat $(REPEAT) --run-id $(RUN_ID); \
	status=$$?; \
	$(BENCHCTL) normalize $(RUN_ID); \
	$(BENCHCTL) compare --allow-missing results/normalized/$(RUN_ID).json; \
	$(BENCHCTL) report results/normalized/$(RUN_ID).json; \
	echo "make $@ finished at $$(date -u +%Y-%m-%dT%H:%M:%SZ)"; \
	exit $$status

compare-all-docker: DB_PORT = $(if $(filter undefined,$(DB_PORT_ORIGIN)),$(COMPARE_ALL_DOCKER_DB_PORT),$(DB_PORT_INPUT))
compare-all-docker: REDIS_PORT = $(if $(filter undefined,$(REDIS_PORT_ORIGIN)),$(COMPARE_ALL_DOCKER_REDIS_PORT),$(REDIS_PORT_INPUT))
compare-all-docker: db-seed redis-seed
	@echo "make compare-all-docker started at $$(date -u +%Y-%m-%dT%H:%M:%SZ)"
	$(MAKE) docker-web-build
	@mkdir -p .cache
	@echo "$(RUN_ID)" > $(LAST_RUN_FILE)
	@set +e; \
	PERFBENCH_DB_PORT=$(DB_PORT) PERFBENCH_REDIS_PORT=$(REDIS_PORT) PERFBENCH_DOCKER_NETWORK=$(DOCKER_NETWORK) $(BENCHCTL) run --web-runner docker --repeat $(REPEAT) --run-id $(RUN_ID); \
	status=$$?; \
	$(BENCHCTL) normalize $(RUN_ID); \
	$(BENCHCTL) compare --allow-missing results/normalized/$(RUN_ID).json; \
	$(BENCHCTL) report results/normalized/$(RUN_ID).json; \
	echo "make $@ finished at $$(date -u +%Y-%m-%dT%H:%M:%SZ)"; \
	exit $$status

normalize:
	@if [ "$(origin RUN_ID)" = "command line" ] || [ "$(origin RUN_ID)" = "environment" ]; then id="$(RUN_ID)"; else test -f $(LAST_RUN_FILE) || (echo "No previous run id found. Run make smoke or make full first, or pass RUN_ID=..." && exit 1); id=$$(cat $(LAST_RUN_FILE)); fi; $(BENCHCTL) normalize $$id

summarize:
	@if [ "$(origin RUN_ID)" = "command line" ] || [ "$(origin RUN_ID)" = "environment" ]; then id="$(RUN_ID)"; else test -f $(LAST_RUN_FILE) || (echo "No previous run id found. Run make smoke or make full first, or pass RUN_ID=..." && exit 1); id=$$(cat $(LAST_RUN_FILE)); fi; $(BENCHCTL) summarize results/normalized/$$id.json

resources-latest:
	@if [ "$(origin RUN_ID)" = "command line" ] || [ "$(origin RUN_ID)" = "environment" ]; then id="$(RUN_ID)"; else test -f $(LAST_RUN_FILE) || (echo "No previous run id found. Run make compare-web or make compare-web-docker first, or pass RUN_ID=..." && exit 1); id=$$(cat $(LAST_RUN_FILE)); fi; $(BENCHCTL) resources results/normalized/$$id.json

report-latest:
	@if [ "$(origin RUN_ID)" = "command line" ] || [ "$(origin RUN_ID)" = "environment" ]; then id="$(RUN_ID)"; else test -f $(LAST_RUN_FILE) || (echo "No previous run id found. Run make compare-web or make compare-web-docker first, or pass RUN_ID=..." && exit 1); id=$$(cat $(LAST_RUN_FILE)); fi; $(BENCHCTL) report results/normalized/$$id.json; echo "make $@ finished at $$(date -u +%Y-%m-%dT%H:%M:%SZ)"

publish-report-gist:
	@args=""; \
	if [ -n "$(REPORT)" ]; then \
		args="--path $(REPORT)"; \
	elif [ "$(origin RUN_ID)" = "command line" ] || [ "$(origin RUN_ID)" = "environment" ]; then \
		args="--run-id $(RUN_ID)"; \
	fi; \
	$(PYTHON) tools/publish_report_gist.py $$args --visibility $(GIST_VISIBILITY)

compare-latest:
	@if [ "$(origin RUN_ID)" = "command line" ] || [ "$(origin RUN_ID)" = "environment" ]; then id="$(RUN_ID)"; else test -f $(LAST_RUN_FILE) || (echo "No previous run id found. Run make compare-smoke or make compare first, or pass RUN_ID=..." && exit 1); id=$$(cat $(LAST_RUN_FILE)); fi; $(BENCHCTL) compare results/normalized/$$id.json

smoke-dotnet: db-seed redis-seed
	@mkdir -p .cache
	@echo "$(RUN_ID)" > $(LAST_RUN_FILE)
	PERFBENCH_DB_PORT=$(DB_PORT) PERFBENCH_REDIS_PORT=$(REDIS_PORT) $(BENCHCTL) run --platform dotnet --smoke --run-id $(RUN_ID)
	$(BENCHCTL) normalize $(RUN_ID)
	$(BENCHCTL) summarize results/normalized/$(RUN_ID).json

smoke-java: db-seed redis-seed
	@mkdir -p .cache
	@echo "$(RUN_ID)" > $(LAST_RUN_FILE)
	PERFBENCH_DB_PORT=$(DB_PORT) PERFBENCH_REDIS_PORT=$(REDIS_PORT) $(BENCHCTL) run --platform java --smoke --run-id $(RUN_ID)
	$(BENCHCTL) normalize $(RUN_ID)
	$(BENCHCTL) summarize results/normalized/$(RUN_ID).json

smoke-go: db-seed redis-seed
	@mkdir -p .cache
	@echo "$(RUN_ID)" > $(LAST_RUN_FILE)
	PERFBENCH_DB_PORT=$(DB_PORT) PERFBENCH_REDIS_PORT=$(REDIS_PORT) $(BENCHCTL) run --platform go --smoke --run-id $(RUN_ID)
	$(BENCHCTL) normalize $(RUN_ID)
	$(BENCHCTL) summarize results/normalized/$(RUN_ID).json

run-dotnet: db-seed redis-seed
	@mkdir -p .cache
	@echo "$(RUN_ID)" > $(LAST_RUN_FILE)
	PERFBENCH_DB_PORT=$(DB_PORT) PERFBENCH_REDIS_PORT=$(REDIS_PORT) $(BENCHCTL) run --platform dotnet --run-id $(RUN_ID)
	$(BENCHCTL) normalize $(RUN_ID)
	$(BENCHCTL) summarize results/normalized/$(RUN_ID).json

run-java: db-seed redis-seed
	@mkdir -p .cache
	@echo "$(RUN_ID)" > $(LAST_RUN_FILE)
	PERFBENCH_DB_PORT=$(DB_PORT) PERFBENCH_REDIS_PORT=$(REDIS_PORT) $(BENCHCTL) run --platform java --run-id $(RUN_ID)
	$(BENCHCTL) normalize $(RUN_ID)
	$(BENCHCTL) summarize results/normalized/$(RUN_ID).json

run-go: db-seed redis-seed
	@mkdir -p .cache
	@echo "$(RUN_ID)" > $(LAST_RUN_FILE)
	PERFBENCH_DB_PORT=$(DB_PORT) PERFBENCH_REDIS_PORT=$(REDIS_PORT) $(BENCHCTL) run --platform go --run-id $(RUN_ID)
	$(BENCHCTL) normalize $(RUN_ID)
	$(BENCHCTL) summarize results/normalized/$(RUN_ID).json
