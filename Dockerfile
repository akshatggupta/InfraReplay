FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml README.md ./
COPY infrareplay ./infrareplay
COPY migrations ./migrations
COPY alembic.ini ./

RUN pip install --no-cache-dir -e ".[test]"

# api | dashboard, selected by docker-compose command.
EXPOSE 8000 8080

CMD ["uvicorn", "infrareplay.api.app:app", "--host", "0.0.0.0", "--port", "8000"]
