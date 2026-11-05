FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 TZ=Asia/Taipei
WORKDIR /app
COPY pyproject.toml ./
RUN pip install --no-cache-dir "fastapi>=0.115" "uvicorn[standard]>=0.30" "sqlalchemy>=2.0.30" "psycopg[binary]>=3.2" \
    "alembic>=1.13" "pydantic>=2.7" "pydantic-settings>=2.3" "httpx>=0.27" "pandas>=2.2" "numpy>=1.26" "apscheduler>=3.10,<4" \
    "pytest>=8"
COPY . .
# set by scripts\build-release.ps1: an image always says which commit it is
ARG AQT_BUILD_COMMIT=unknown
ENV AQT_BUILD_COMMIT=$AQT_BUILD_COMMIT
LABEL org.opencontainers.image.revision=$AQT_BUILD_COMMIT
RUN useradd -m -u 1000 aqt && mkdir -p /data/raw /data/logs /data/reports && chown -R aqt /app /data
USER aqt
CMD ["python", "-m", "aqt.cli", "api"]
