.PHONY: install lint test test-unit test-integration build validate deploy clean

PYTHON  ?= python
SAM     ?= sam
STACK   ?= aws-vpc-provisioning-api
REGION  ?= ap-south-1

# ── Install ──────────────────────────────────────────────────────────────────
install:
	$(PYTHON) -m pip install --upgrade pip
	$(PYTHON) -m pip install -e ".[dev]"

# ── Lint / Format ────────────────────────────────────────────────────────────
lint:
	$(PYTHON) -m ruff check src tests
	$(PYTHON) -m ruff format --check src tests

format:
	$(PYTHON) -m ruff check --fix src tests
	$(PYTHON) -m ruff format src tests

# ── Tests ────────────────────────────────────────────────────────────────────
test:
	$(PYTHON) -m pytest tests/ -v

test-unit:
	$(PYTHON) -m pytest tests/unit/ -v

test-integration:
	$(PYTHON) -m pytest tests/integration/ -v -m integration

test-contract:
	$(PYTHON) -m pytest tests/contract/ -v -m contract

# ── SAM ──────────────────────────────────────────────────────────────────────
validate:
	$(SAM) validate --region $(REGION)

build:
	$(SAM) build --use-container

deploy-guided:
	$(SAM) deploy --guided --region $(REGION)

deploy:
	$(SAM) deploy --region $(REGION)

# ── Diagrams ─────────────────────────────────────────────────────────────────
diagrams:
	dot -Tpng docs/diagrams/architecture.dot -o docs/diagrams/architecture.png
	dot -Tpng docs/diagrams/workflow.dot     -o docs/diagrams/workflow.png

# ── Clean ────────────────────────────────────────────────────────────────────
clean:
	find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null; true
	find . -type d -name ".pytest_cache" -exec rm -rf {} + 2>/dev/null; true
	find . -type d -name "htmlcov" -exec rm -rf {} + 2>/dev/null; true
	find . -name "*.pyc" -delete 2>/dev/null; true
	rm -rf .aws-sam/ 2>/dev/null; true
