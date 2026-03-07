FROM python:3.12-slim

RUN apt-get update && apt-get install -y git ripgrep && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install dependencies
COPY github_app/requirements.txt github_app/requirements.txt
RUN pip install --no-cache-dir -r github_app/requirements.txt

# Copy both packages (review engine + github app)
COPY pr_review_agent/ pr_review_agent/
COPY github_app/ github_app/

# Organism file is optional (set ORGANISM_PATH env to use)
# Created at runtime or via evolver/run.py

EXPOSE 8080
CMD ["uvicorn", "github_app.app:app", "--host", "0.0.0.0", "--port", "8080"]
