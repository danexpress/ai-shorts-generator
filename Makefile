.DEFAULT_GOAL := help

UV ?= uv
HOST ?= 127.0.0.1
PORT ?= 8000
FRONTEND_PORT ?= 3000
# Optional backend extras kept installed by `make install` / `make run`: Kokoro (the
# default Story Mode voice) and FLUX via mflux (AI scene pictures, Apple silicon only).
# Use EXTRAS= for a smaller install, or add --extra align.
EXTRAS ?= --extra kokoro --extra images
BACKEND_DIR := $(dir $(abspath $(lastword $(MAKEFILE_LIST))))backend
FRONTEND_DIR := $(dir $(abspath $(lastword $(MAKEFILE_LIST))))frontend
# backend/.env configures the servers when present. Tests deliberately don't load it.
ENV_FILE := $(if $(wildcard $(BACKEND_DIR)/.env),--env-file .env)

.PHONY: help install run frontend test test-frontend lint format worker

help:
	@echo "make run      Start the backend (auto-reload) and the video processing worker"
	@echo "make worker   Run the social publishing worker (same environment as backend)"
	@echo "make install  Install backend dependencies (EXTRAS=\"--extra kokoro\" by default)"
	@echo "make frontend Serve the frontend at http://localhost:$(FRONTEND_PORT) (run in a second terminal)"
	@echo "make test     Run backend tests"
	@echo "make test-frontend Run frontend service and HTTP integration tests"
	@echo "make lint     Check code style and formatting"
	@echo "make format   Format backend code and tests"
	@echo "Override server settings: make run HOST=127.0.0.1 PORT=8001"

# Social publishing worker. (Video processing runs with `make run`.)
worker:
	cd "$(BACKEND_DIR)" && $(UV) run --locked $(ENV_FILE) python -m app.social.worker

install:
	cd "$(BACKEND_DIR)" && $(UV) sync --locked $(EXTRAS)

# API plus the video processing worker: uploads are transcribed and analyzed by the
# worker, so the two always run together. Ctrl-C stops both. Both reload when backend
# code changes (uvicorn --reload; watchfiles restarts the worker).
run:
	cd "$(BACKEND_DIR)" && $(UV) sync --locked --quiet $(EXTRAS) && trap 'kill 0' INT TERM; \
		$(UV) run --locked $(ENV_FILE) watchfiles --filter python "python -m app.jobs.worker" app & \
		$(UV) run --locked $(ENV_FILE) uvicorn app.main:app --reload --host "$(HOST)" --port "$(PORT)" & \
		wait

frontend:
	@echo "Open http://localhost:$(FRONTEND_PORT)"
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
