FROM python:3.12-slim
WORKDIR /srv/app
COPY pyproject.toml README.md alembic.ini ./
COPY app ./app
COPY migrations ./migrations
COPY scripts ./scripts
COPY evaluations ./evaluations
RUN pip install --no-cache-dir . && useradd --uid 10001 --create-home onboarding
USER onboarding
ENV PYTHONDONTWRITEBYTECODE=1
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
