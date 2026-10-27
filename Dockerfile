FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HOST=0.0.0.0 \
    PORT=8569 \
    RELOAD=false

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt

RUN groupadd --system app && useradd --system --gid app app \
    && mkdir -p /app/.cache \
    && chown -R app:app /app

COPY --chown=app:app . .
USER app

EXPOSE 8569

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "from urllib.request import urlopen; urlopen('http://127.0.0.1:8569/api/health', timeout=4)" || exit 1

CMD ["python", "run.py"]
