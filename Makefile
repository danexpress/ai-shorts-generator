.DEFAULT_GOAL := help

UV ?= uv
HOST ?= 127.0.0.1
PORT ?= 8000
FRONTEND_PORT ?= 3000
BACKEND_DIR := $(dir $(abspath $(lastword $(MAKEFILE_LIST))))backend
FRONTEND_DIR := $(dir $(abspath $(lastword $(MAKEFILE_LIST))))frontend

.PHONY: help install run frontend test test-frontend lint format worker

help:
	@echo "make run      Start the backend with auto-reload (installs dependencies as needed)"
	@echo "make worker   Run the processing and social publishing workers (same environment as backend)"
	@echo "make install  Install backend dependencies"
	@echo "make frontend Serve the frontend at http://127.0.0.1:$(FRONTEND_PORT) (run in a second terminal)"
	@echo "make test     Run backend tests"
	@echo "make test-frontend Run frontend service and HTTP integration tests"
	@echo "make lint     Check code style and formatting"
	@echo "make format   Format backend code and tests"
	@echo "Override server settings: make run HOST=127.0.0.1 PORT=8001"

# Runs the video processing worker (transcription/analysis) and the social publisher.
worker:
	cd "$(BACKEND_DIR)" && trap 'kill 0' INT TERM; \
		$(UV) run --locked python -m app.jobs.worker & \
		$(UV) run --locked python -m app.social.worker & \
		wait

install:
	cd "$(BACKEND_DIR)" && $(UV) sync --locked

run:
	cd "$(BACKEND_DIR)" && $(UV) run --locked uvicorn app.main:app --reload --host "$(HOST)" --port "$(PORT)"

frontend:
	@echo "Open http://127.0.0.1:$(FRONTEND_PORT)"
	cd "$(BACKEND_DIR)" && $(UV) run --locked python -m http.server "$(FRONTEND_PORT)" --bind 127.0.0.1 --directory "$(FRONTEND_DIR)"

test:
	cd "$(BACKEND_DIR)" && $(UV) run --locked pytest -q

test-frontend:
	node "$(FRONTEND_DIR)/tests.js"
	node --test "$(FRONTEND_DIR)/http-client.test.cjs"

lint:
	cd "$(BACKEND_DIR)" && $(UV) run --locked ruff check app tests
	cd "$(BACKEND_DIR)" && $(UV) run --locked ruff format --check app tests

format:
	cd "$(BACKEND_DIR)" && $(UV) run --locked ruff format app tests
