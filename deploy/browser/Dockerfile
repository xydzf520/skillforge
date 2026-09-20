FROM ubuntu:22.04

ENV DEBIAN_FRONTEND=noninteractive
ENV DISPLAY=:99
ENV LANG=zh_CN.UTF-8

# 基础工具 + 中文支持
RUN apt-get update && apt-get install -y --no-install-recommends \
    wget curl gnupg ca-certificates \
    locales fonts-wqy-zenhei fonts-noto-cjk \
    supervisor xvfb x11vnc novnc websockify socat \
    x11-xserver-utils \
    && locale-gen zh_CN.UTF-8 \
    && rm -rf /var/lib/apt/lists/*

# 安装 Google Chrome（真实浏览器，非 Chromium）
RUN wget -qO- https://dl.google.com/linux/linux_signing_key.pub | gpg --dearmor -o /usr/share/keyrings/google-chrome.gpg \
    && echo "deb [arch=amd64 signed-by=/usr/share/keyrings/google-chrome.gpg] http://dl.google.com/linux/chrome/deb/ stable main" > /etc/apt/sources.list.d/google-chrome.list \
    && apt-get update && apt-get install -y --no-install-recommends google-chrome-stable \
    && rm -rf /var/lib/apt/lists/*

# 安装 Python websockets（反检测脚本注入用）
RUN apt-get update && apt-get install -y --no-install-recommends python3-pip \
    && pip3 install --no-cache-dir websockets \
    && rm -rf /var/lib/apt/lists/*

# 安装 Node.js 20 + OpenCLI
RUN curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && npm install -g @jackwener/opencli \
    && rm -rf /var/lib/apt/lists/*

# 创建非 root 用户（Chrome 不允许 root 运行）
RUN groupadd -r chrome && useradd -r -g chrome -m -d /home/chrome chrome

# 复制配置
COPY supervisord.conf /etc/supervisor/conf.d/supervisord.conf
COPY entrypoint.sh /entrypoint.sh
COPY anti-detect.ts /opt/anti-detect.js
RUN chmod +x /entrypoint.sh

# Chrome profile 持久化目录
VOLUME /home/chrome/data

# noVNC(:6080) + CDP(:9222)
EXPOSE 6080 9222

ENTRYPOINT ["/entrypoint.sh"]
