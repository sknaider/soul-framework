FROM nvidia/cuda@sha256:6e43a6b02e5f16e4a715953be8b29c40acbe84aed11a61357ef0fc899967fbd9

ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates python3 python3-pip python3-venv \
    && rm -rf /var/lib/apt/lists/*

RUN useradd --create-home --uid 1001 qa \
    && mkdir -p /qa /artifacts /payload /payload-source /source /source-archive \
    && chown -R qa:qa /qa /artifacts /payload-source /source-archive

COPY bootstrap.sh run-e2e.sh /qa/
RUN chmod 0755 /qa/bootstrap.sh /qa/run-e2e.sh

USER root
WORKDIR /qa
ENTRYPOINT ["/qa/bootstrap.sh"]
