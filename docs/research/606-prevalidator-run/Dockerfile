FROM mono:6.12
RUN sed -i 's|deb.debian.org|archive.debian.org|; s|security.debian.org|archive.debian.org|; /buster-updates/d' /etc/apt/sources.list && apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends mono-vbnc mono-utils libgdiplus xvfb xdotool imagemagick x11-utils fonts-dejavu-core && rm -rf /var/lib/apt/lists/*
RUN useradd -m runner
USER runner
WORKDIR /home/runner
