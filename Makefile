.PHONY: help bootstrap-env dev test lint format

VENV := .venv
PY := $(VENV)/bin/python
UVICORN := $(VENV)/bin/uvicorn
PIP := $(VENV)/bin/pip

help:  ## Lista targets disponiveis
	@awk 'BEGIN {FS = ":.*?## "} /^[a-zA-Z_-]+:.*?## / {printf "  %-20s %s\n", $$1, $$2}' $(MAKEFILE_LIST)

$(VENV):  ## Cria virtualenv Python 3.12 (so se nao existir)
	python3.12 -m venv $(VENV)
	$(PIP) install -U pip
	$(PIP) install -e ".[dev]"

bootstrap-env:  ## Regenera .env.local com secrets atuais do GCP Secret Manager
	./scripts/dev/bootstrap-local-env.sh

dev: $(VENV)  ## Sobe uvicorn local em :8000 com .env.local carregado
	@test -f .env.local || { echo "✗ .env.local ausente. Rode: make bootstrap-env"; exit 1; }
	@set -a; . ./.env.local; set +a; \
	  $(UVICORN) graphql_api.app:create_app --factory --reload --port 8000

test: $(VENV)  ## Roda suite pytest
	$(VENV)/bin/pytest -q

lint: $(VENV)  ## Roda ruff check
	$(VENV)/bin/ruff check src tests

format: $(VENV)  ## Roda ruff format
	$(VENV)/bin/ruff format src tests
