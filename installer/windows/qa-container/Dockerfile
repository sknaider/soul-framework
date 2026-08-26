FROM nvidia/cuda@sha256:6e43a6b02e5f16e4a715953be8b29c40acbe84aed11a61357ef0fc899967fbd9

ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates python3 python3-cryptography \
    && rm -rf /var/lib/apt/lists/*

RUN useradd --create-home --uid 1001 qa \
    && mkdir -p /qa /artifacts \
    && chown -R qa:qa /qa /artifacts

COPY run-e2e.sh container_preflight.py ollama_probe.py /qa/
RUN chmod 0755 /qa/run-e2e.sh

USER qa
WORKDIR /qa
ENTRYPOINT ["/qa/run-e2e.sh"]
