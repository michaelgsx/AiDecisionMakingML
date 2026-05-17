# Daily train image for Azure Container Apps Job (ai-rag-ml) or any cron runner.
FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY train.py score.py ./
COPY src ./src

ENV PYTHONUNBUFFERED=1
# Default: daily tag + upload to airagblob/logistic
CMD ["python", "train.py", "--daily"]
