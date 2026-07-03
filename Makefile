PYTHON ?= python3
BENCHCTL := $(PYTHON) tools/benchctl/benchctl.py
RUN_ID ?= local-$$(date -u +%Y%m%dT%H%M%SZ)
GOCACHE ?= $(CURDIR)/.cache/go-build
MAVEN_REPO ?= $(CURDIR)/.cache/m2

.PHONY: validate plan env run smoke normalize run-dotnet run-java run-go run-all

validate:
	$(BENCHCTL) validate

plan:
	$(BENCHCTL) plan

env:
	$(BENCHCTL) env

run:
	$(BENCHCTL) run

smoke:
	$(BENCHCTL) run --smoke

normalize:
	$(BENCHCTL) normalize $(RUN_ID)

run-dotnet:
	$(BENCHCTL) run --platform dotnet --run-id $(RUN_ID)

run-java:
	$(BENCHCTL) run --platform java --run-id $(RUN_ID)

run-go:
	$(BENCHCTL) run --platform go --run-id $(RUN_ID)

run-all: run-dotnet run-java run-go
