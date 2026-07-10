FROM python:3.10-slim

# Install Node.js, Git, and Docker CLI tools
RUN apt-get update && apt-get install -y \
    curl \
    git \
    docker.io \
    && curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y nodejs \
    && rm -rf /var/lib/apt/lists/*

# Install Pi coding agent globally with secure flags
RUN npm install -g --ignore-scripts @earendil-works/pi-coding-agent

# Set up an unprivileged agent user
RUN useradd -m -s /bin/bash agent
USER agent
WORKDIR /home/agent

# Copy Pi config (auth is mounted at runtime, not baked in)
COPY --chown=agent:agent .pi/settings.json .pi/models.json .pi/npm/ /home/agent/.pi/

ENTRYPOINT ["/bin/bash"]
