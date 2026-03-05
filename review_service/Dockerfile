FROM python:3.12-slim

# Install git for repo cloning, ripgrep for grep_pattern tool
RUN apt-get update && apt-get install -y git ripgrep && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install review_service dependencies
COPY review_service/requirements.txt review_service_requirements.txt
RUN pip install --no-cache-dir -r review_service_requirements.txt

# Install pr_review_agent dependencies (reuse github_app requirements which overlap)
COPY github_app/requirements.txt github_app_requirements.txt
RUN pip install --no-cache-dir -r github_app_requirements.txt

# Copy all packages
COPY pr_review_agent/ pr_review_agent/
COPY github_app/ github_app/
COPY review_service/ review_service/

# Bundle evolved organism for tuned prompts (optional, set ORGANISM_PATH env to use)
COPY pr_review_agent/evolver/seed_organism_v2.json /app/organism.json

ENV PYTHONPATH=/app

EXPOSE 8080

CMD ["uvicorn", "review_service.app:app", "--host", "0.0.0.0", "--port", "8080"]
