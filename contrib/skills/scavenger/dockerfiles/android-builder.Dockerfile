FROM ghcr.io/cirruslabs/android-sdk:35

# Install OpenJDK 17 (required by Gradle 9.4 for React Native / Expo SDK 58) and Node.js 22 LTS
RUN apt-get update && apt-get install -y --no-install-recommends \
    openjdk-17-jdk-headless \
    curl \
    ca-certificates \
    gnupg \
    && curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/*

ENV JAVA_HOME=/usr/lib/jvm/java-17-openjdk-amd64
ENV PATH=$JAVA_HOME/bin:$PATH

WORKDIR /workspace
