FROM python:3.9.8-slim-bullseye

LABEL author="mybsdc <mybsdc@gmail.com>" \
    maintainer="luolongfei <luolongf@gmail.com>"

ENV TZ Asia/Shanghai
RUN ln -snf /usr/share/zoneinfo/$TZ /etc/localtime && echo $TZ > /etc/timezone

# 非交互式安装，避免告警
ARG DEBIAN_FRONTEND=noninteractive

ARG CHROME_VERSION=96.0.4664.45-1
ARG CHROME_DRIVER_VERSION=96.0.4664.45

ARG CHROME_DOWNLOAD_URL=http://dl.google.com/linux/chrome/deb/pool/main/g/google-chrome-stable/google-chrome-stable_${CHROME_VERSION}_amd64.deb
ARG CHROME_DRIVER_DOWNLOAD_URL=https://chromedriver.storage.googleapis.com/${CHROME_DRIVER_VERSION}/chromedriver_linux64.zip

# set -eux e: 脚本只要发生错误，就终止执行 u: 遇到不存在的变量就会报错，并停止执行 x: 在运行结果之前，先输出执行的那一行命令
RUN set -eux; \
    # 安装基础依赖工具
    apt-get update; \
    apt-get install -y --no-install-recommends \
    fonts-liberation \
    libasound2 \
    libatk-bridge2.0-0 \
    libatk1.0-0 \
    libatspi2.0-0 \
    libcups2 \
    libdbus-1-3 \
    libgtk-3-0 \
    libnspr4 \
    libnss3 \
    libx11-xcb1 \
    libxcomposite1 \
    libxcursor1 \
    libxdamage1 \
    libxfixes3 \
    libxi6 \
    libxrandr2 \
    libxss1 \
    libxtst6 \
    lsb-release \
    libwayland-server0 \
    libgbm1 \
    curl \
    unzip \
    wget \
    xdg-utils \
    xvfb; \
    # 清除非明确安装的推荐的或额外的扩展 configure apt-get to automatically consider those non-explicitly installed suggestions/recommendations as orphans
    apt-get purge -y --auto-remove -o APT::AutoRemove::RecommendsImportant=false; \
    # It removes everything but the lock file from /var/cache/apt/archives/ and /var/cache/apt/archives/partial/
    apt-get clean; \
    # 删除包信息缓存
    rm -rf /var/lib/apt/lists/*

# 下载并安装 Chrome
RUN wget --no-verbose -O /tmp/chrome.deb "${CHROME_DOWNLOAD_URL}"; \
    apt-get install -yf /tmp/chrome.deb; \
    /usr/bin/google-chrome --version; \
    rm -f /tmp/chrome.deb

# 下载并启用 ChromeDriver
RUN wget --no-verbose -O chromedriver.zip "${CHROME_DRIVER_DOWNLOAD_URL}"; \
    unzip chromedriver.zip; \
    rm chromedriver.zip; \
    mv chromedriver /usr/bin/chromedriver; \
    chmod +x /usr/bin/chromedriver; \
    /usr/bin/chromedriver --version

WORKDIR /app

COPY . ./

RUN pip install -i https://pypi.tuna.tsinghua.edu.cn/simple --no-cache-dir -r requirements.txt

VOLUME ["/conf", "/app/logs"]

COPY docker-entrypoint.sh /usr/local/bin/
ENTRYPOINT ["docker-entrypoint.sh"]

CMD ["crond", "-f"]
