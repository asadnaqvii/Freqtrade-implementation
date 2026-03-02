FROM python:3.11-slim

# Install TA-Lib C library dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install TA-Lib C library
RUN cd /tmp && \
    curl -L -o ta-lib-0.4.0-src.tar.gz https://github.com/TA-Lib/ta-lib/releases/download/v0.4.0/ta-lib-0.4.0-src.tar.gz && \
    tar -xzf ta-lib-0.4.0-src.tar.gz && \
    cd ta-lib/ && \
    ./configure --prefix=/usr && \
    make && \
    make install && \
    cd / && rm -rf /tmp/ta-lib /tmp/ta-lib-0.4.0-src.tar.gz

WORKDIR /app

# Install Python dependencies
RUN pip install --no-cache-dir numpy
RUN pip install --no-cache-dir TA-Lib
RUN pip install --no-cache-dir freqtrade

# Install FreqUI web dashboard
RUN freqtrade install-ui

# Copy project files
COPY strategies/ ./strategies/
COPY config/ ./config/
COPY render_start.py ./start.py

# Create user_data directories
RUN mkdir -p user_data/strategies user_data/data user_data/logs user_data/backtest_results

# Copy strategies to user_data as well
RUN cp strategies/*.py user_data/strategies/

ENV PORT=8080

CMD ["python", "start.py"]
