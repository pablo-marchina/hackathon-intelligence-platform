.PHONY: install-python lint format-check typecheck test verify source-coverage db-up db-down db-migrate api worker web

install-python:
	python -m pip install -e '.[dev]'

lint:
	ruff check apps packages scripts

format-check:
	ruff format --check apps packages scripts

typecheck:
	mypy apps/api/src apps/worker/src packages/shared/src

test:
	pytest -q

verify: lint format-check typecheck test
	python scripts/verify_repository.py

db-up:
	docker compose -f infra/docker/compose.yml up -d postgres redis minio minio-init

db-down:
	docker compose -f infra/docker/compose.yml down

db-migrate:
	alembic -c db/alembic.ini upgrade head

api:
	uvicorn hackathon_api.main:app --reload --app-dir apps/api/src

worker:
	celery -A hackathon_worker.app:celery_app worker --loglevel=INFO

web:
	cd apps/web && npm run dev

source-coverage:
	python scripts/source_coverage_report.py --format markdown
