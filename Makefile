.PHONY: demo up down test test-unit test-integration verify-incidents demo-traces demo-jaeger docs-svg test-live fmt lint typecheck install

install:
	uv sync

up:
	docker compose up -d

down:
	docker compose down

test:
	uv run pytest -v

test-unit:
	uv run pytest tests/unit -v

test-integration:
	uv run pytest tests/integration -v

test-live:
	uv run --env-file .env pytest tests/live -m live -v

verify-incidents:
	uv run python scripts/verify_incident_detection.py

demo-traces:
	uv run python scripts/demo_traces.py

demo-jaeger:
	uv run python scripts/demo_traces.py --jaeger

# Full demo: Jaeger, scripted scenarios, one real-model turn when .env holds a key.
ENV_FILE := $(if $(wildcard .env),--env-file .env,)

demo: up
	uv run $(ENV_FILE) python scripts/demo_traces.py --jaeger --wait --live

docs-svg:
	uv run python scripts/render_docs_svg.py

fmt:
	uv run ruff format .
	uv run ruff check --fix .

lint:
	uv run ruff check .

typecheck:
	uv run mypy src
