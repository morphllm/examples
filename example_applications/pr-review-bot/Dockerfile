FROM python:3.12-slim

# git to clone the PR, ripgrep for WarpGrep's search tool
RUN apt-get update && apt-get install -y git ripgrep && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8080
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8080"]
