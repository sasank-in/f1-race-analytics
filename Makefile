PY := .venv/Scripts/python.exe
COMPOSE := docker compose -f docker/docker-compose.yml

.PHONY: up down logs doctor test unit lint fmt typecheck migrate psql clean

up:            ## start database + cache
	$(COMPOSE) up -d

down:          ## stop the stack (keeps data)
	$(COMPOSE) down

logs:
	$(COMPOSE) logs -f --tail=100

doctor:        ## verify every dependency is reachable
	$(PY) -m f1x.cli doctor

test:
	$(PY) -m pytest tests -v

unit:          ## unit tests only, no database required
	$(PY) -m pytest tests -m "not integration" -v

lint:
	$(PY) -m ruff check backend/src tests

fmt:
	$(PY) -m ruff check --fix backend/src tests
	$(PY) -m ruff format backend/src tests

typecheck:
	$(PY) -m mypy backend/src/f1x

migrate:       ## apply migrations
	cd backend && ../$(PY) -m alembic upgrade head

psql:
	docker exec -it f1x-db psql -U f1x -d f1x

clean:         ## stop the stack AND delete all data
	$(COMPOSE) down -v
