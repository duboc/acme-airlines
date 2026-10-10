FROM python:3.12-slim
# scion CLI pinned to the hub's release, checksum-verified (SHA256SUMS of nightly-20261009).
ARG SCION_VERSION=nightly-20261009
ARG SCION_SHA256=12495ef9bba6611aaaf3573f9d1b67bdd05302767895fc6f6697bfcc736a5860
ADD https://github.com/GoogleCloudPlatform/scion/releases/download/${SCION_VERSION}/scion-linux-amd64.tar.gz /tmp/scion.tgz
RUN echo "${SCION_SHA256}  /tmp/scion.tgz" | sha256sum -c - \
 && mkdir -p /tmp/scion && tar xzf /tmp/scion.tgz -C /tmp/scion \
 && install -m 0755 "$(find /tmp/scion -type f -name scion | head -1)" /usr/local/bin/scion \
 && rm -rf /tmp/scion /tmp/scion.tgz && scion --help >/dev/null
WORKDIR /app
COPY main.py .
ENV HOME=/tmp
USER 65534
CMD ["python", "main.py"]
