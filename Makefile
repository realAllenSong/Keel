.PHONY: app release e2e install engine run dev-demo api test lint seed-demo hooks

NOTARY_PROFILE ?= ThunderTalk-notary
E2E_OUT ?= /tmp/keel-e2e

app: ## Keel.app: SwiftUI + bundled Python engine, signed (NOTARIZE=1 is opt-in)
	cd desktop && uv run --python 3.12 python ../apple/build.py

release: ## Public build: notarized Keel.app + dist/Keel.dmg (website) + dist/Keel-macOS.zip (updates)
	cd desktop && KEEL_CHANNEL=release NOTARIZE=1 NOTARY_PROFILE=$(NOTARY_PROFILE) uv run --python 3.12 python ../apple/build.py

e2e: ## A new user's onboarding end to end on synthetic statements (fresh home, port 8799)
	uv run --project backend python tools/e2e_onboarding.py $(E2E_OUT)

install: ## Build Keel.app and replace /Applications/Keel.app (data is kept and backed up)
	scripts/install-app.sh

run: ## Run the SwiftUI app from source against the engine started by `make api`
	cd apple && KEEL_ENGINE_URL=http://127.0.0.1:8711 swift run Keel

api: ## The engine's API on 127.0.0.1:8711 (your real ledger, data/ledger.db)
	cd backend && uv run pf serve --reload

DEMO_ENV = PF_DB=$(CURDIR)/data/demo.db PF_RAW_DIR=$(CURDIR)/data/demo-raw

seed-demo: ## Rebuild the synthetic demo ledger (data/demo.db) from fixtures
	rm -rf data/demo.db data/demo.db-wal data/demo.db-shm data/demo-raw
	cd backend && $(DEMO_ENV) uv run pf import ../fixtures/demo/statements/* > /dev/null
	cd backend && $(DEMO_ENV) uv run pf decisions import ../fixtures/demo/decisions.yaml

dev-demo: ## The engine on the synthetic demo ledger; then `make run` in another terminal
	cd backend && $(DEMO_ENV) uv run pf serve --reload

test:
	cd backend && uv run pytest -q
	cd desktop && uv run --python 3.12 pytest -q tests
	python3 apple/scripts/check_l10n.py
	@if command -v swift >/dev/null; then cd apple && swift test; fi

lint:
	cd backend && uv run ruff check . && uv run ruff format --check . && uv run pyright

hooks:
	cd backend && uv run pre-commit install --config ../.pre-commit-config.yaml
