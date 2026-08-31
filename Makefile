PROJECT_ID ?= $(shell gcloud config get-value project)
REGION ?= us-central1
IMAGE_REPO ?= it-helpdesk-repo
IMAGE := $(REGION)-docker.pkg.dev/$(PROJECT_ID)/$(IMAGE_REPO)/agent:latest

# Local Development Commands
install:
	uv sync

test:
	uv run pytest tests/ -v

run-server:
	uv run python main.py --mode serve

run-cli:
	uv run python main.py --mode cli

# Deploy via Cloud Build & Cloud Run
docker-deploy:
	@echo "🚀 Building and deploying IT Helpdesk Agent to $(PROJECT_ID) via Cloud Build..."
	gcloud builds submit --tag $(IMAGE) --project $(PROJECT_ID) .
	gcloud run services update it-helpdesk-agent \
		--image $(IMAGE) \
		--region $(REGION) \
		--project $(PROJECT_ID) \
		--labels component=it-helpdesk-agent

# Knowledge Base Ingestion Pipeline
ingest-kb-dry:
	@echo "🔍 Simulating Knowledge Base Ingestion (Dry Run)..."
	uv run python scripts/ingest_knowledge_base.py --source-dir data/knowledge_base/ --dry-run --test-query "Lỗi tạo đơn hàng SAP ME21N"

ingest-kb:
	@echo "📥 Ingesting Customer Documentation into BigQuery Vector Search..."
	uv run python scripts/ingest_knowledge_base.py --source-dir data/knowledge_base/ --project-id $(PROJECT_ID)
