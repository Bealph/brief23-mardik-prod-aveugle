.PHONY: up down test test-unit test-integration verify-incidents demo-traces demo-jaeger docs-svg fmt lint typecheck install

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

verify-incidents:
	uv run python scripts/verify_incident_detection.py

demo-traces:
	uv run python scripts/demo_traces.py

demo-jaeger:
	uv run python scripts/demo_traces.py --jaeger

docs-svg:
	uv run python scripts/render_docs_svg.py

fmt:
	uv run ruff format .
	uv run ruff check --fix .

lint:
	uv run ruff check .

typecheck:
	uv run mypy src
