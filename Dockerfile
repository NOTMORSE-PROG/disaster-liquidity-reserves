# ReserveBound: the reserve engine, quantum studies and browser app in one image.
#   docker build -t reservebound .
#   docker run --rm -p 8765:8765 reservebound      then open http://localhost:8765
# Build with --build-arg WITH_QUANTUM=0 for a smaller image without Qiskit.
FROM python:3.12-slim

ARG WITH_QUANTUM=1
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/src

WORKDIR /app
COPY requirements.txt requirements-quantum.txt ./
RUN python -m pip install --no-cache-dir -r requirements.txt \
    && if [ "$WITH_QUANTUM" = "1" ]; then python -m pip install --no-cache-dir -r requirements-quantum.txt; fi

COPY LICENSE NOTICE README.md pyproject.toml ./
COPY benchmark ./benchmark
COPY src ./src
COPY results ./results

RUN useradd --create-home --uid 10001 reserve
USER reserve

EXPOSE 8765
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8765/api/health', timeout=4)"
CMD ["python", "-m", "liquidity_reserves.app", "--host", "0.0.0.0", "--port", "8765"]
