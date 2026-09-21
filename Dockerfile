FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml README.md ./
COPY infrareplay ./infrareplay
COPY examples ./examples
COPY migrations ./migrations
COPY alembic.ini ./

RUN pip install --no-cache-dir -e ".[test]"

# api | dashboard | demo shop, selected by docker-compose command.
# 8081 is where the capture proxy listens inside the api container.
EXPOSE 8000 8080 8081 3000

CMD ["uvicorn", "infrareplay.api.app:app", "--host", "0.0.0.0", "--port", "8000"]
