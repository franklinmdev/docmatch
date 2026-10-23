# DGII's 606 pre-validator under Mono, from #217's research run
# (`research/606-prevalidator-run`, b1e0aa2). The image holds no DGII file:
# `Pre_Validado.zip` is mounted read-only at run time, checked against its
# pinned SHA-256 and unpacked inside the container, which runs with
# `--network none`.
#
# mono:6.12 pinned by digest (Mono 6.12.0.182, Debian buster). Buster is
# archived, so its packages come from archive.debian.org and no longer move.
FROM mono@sha256:34d816779b1248b5cfd095770b64ecbaf1798e2aca693a91c11a018dce9c7ad5
RUN sed -i 's|deb.debian.org|archive.debian.org|; s|security.debian.org|archive.debian.org|; /buster-updates/d' /etc/apt/sources.list \
    && apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
       mono-vbnc libgdiplus xvfb xauth cabextract unzip fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*
COPY Harness.cs prevalidate.sh /opt/harness/
RUN mcs -nologo -r:System.Windows.Forms.dll -r:System.Drawing.dll \
       -out:/opt/harness/Harness.exe /opt/harness/Harness.cs \
    && chmod 755 /opt/harness/prevalidate.sh
# The form's messages carry accents; Mono's console writes UTF-8 under this.
ENV LANG=C.UTF-8
RUN useradd -m runner
USER runner
WORKDIR /home/runner
ENTRYPOINT ["/opt/harness/prevalidate.sh"]
