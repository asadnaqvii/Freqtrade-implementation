# Container image for any of the three services. Which one you get depends on
# the command, not the image.
#
#   bot     python render_start.py
#   app     uvicorn app.api.main:app --host 0.0.0.0 --port 8080
#   worker  python -m app.worker.main
#
# The TA-Lib C library build that used to be here is gone: TA-Lib publishes
# prebuilt wheels now, so the image no longer needs a compiler and builds in a
# fraction of the time.

FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# curl is used by the container healthcheck; nothing else needs a toolchain.
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements-render.txt ./
RUN pip install --upgrade pip && pip install -r requirements-render.txt

# FreqUI, for anyone reaching the bot over the private network.
RUN freqtrade install-ui

COPY app/ ./app/
COPY strategies/ ./strategies/
COPY config/ ./config/
COPY db/ ./db/
COPY scripts/ ./scripts/
COPY render_start.py ./

RUN mkdir -p user_data/strategies user_data/data user_data/logs user_data/backtest_results \
    && cp strategies/*.py user_data/strategies/ 2>/dev/null || true

# Run as a non-root user: this process holds exchange API keys.
RUN useradd --create-home --uid 10001 freqtrade \
    && chown -R freqtrade:freqtrade /app
USER freqtrade

ENV PORT=8080
EXPOSE 8080

CMD ["python", "render_start.py"]
