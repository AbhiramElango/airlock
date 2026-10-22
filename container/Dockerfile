# Agent + Ollama in one image. Code, demo repo and model weights are mounted at run time.
# ping loses its file capability (refused under cap_drop ALL); it uses unprivileged ICMP instead.
FROM ollama/ollama:0.40.6

RUN apt-get update \
 && apt-get install -y --no-install-recommends python3 python3-venv git curl iputils-ping dnsutils iproute2 libcap2-bin \
 && rm -rf /var/lib/apt/lists/* \
 && { setcap -r /usr/bin/ping || true; } \
 && python3 -m venv /opt/venv \
 && /opt/venv/bin/pip install --no-cache-dir pytest==9.1.1

COPY entrypoint.sh /usr/local/bin/airlock-entrypoint

ENV PATH=/opt/venv/bin:$PATH AIRLOCK_PYTHON=/opt/venv/bin/python HOME=/tmp \
    OLLAMA_HOST=127.0.0.1:11434 OLLAMA_MODELS=/models OLLAMA_NOPRUNE=1
WORKDIR /app
ENTRYPOINT ["/usr/local/bin/airlock-entrypoint"]
