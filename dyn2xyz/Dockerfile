# dyn2xyz —— Dynmap -> XYZ 瓦片转译代理
#
# 镜像里只有 Python 标准库 + Pillow，没有编译工具链：
# Pillow 在 slim 上有预编译 wheel，装完可以把 pip 缓存清掉。
FROM python:3.12-slim

# PIP_INDEX_URL 可被 --build-arg 覆盖，便于在网络不稳的环境里改用就近镜像源：
#   docker compose build --build-arg PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple dyn2xyz
ARG PIP_INDEX_URL=https://pypi.org/simple

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_INDEX_URL=${PIP_INDEX_URL} \
    PIP_TIMEOUT=60 \
    PIP_RETRIES=10 \
    DYN2XYZ_CONFIG=/config/dyn2xyz.toml

WORKDIR /srv/dyn2xyz

COPY requirements.txt ./
RUN pip install -r requirements.txt

COPY app ./app
COPY config ./config
COPY tools ./tools
COPY tests ./tests

# 以非 root 运行；/config 在 compose 里是只读挂载
RUN useradd --system --create-home --uid 10001 dyn2xyz \
    && chown -R dyn2xyz:dyn2xyz /srv/dyn2xyz
USER dyn2xyz

EXPOSE 8080

# 容器内固定监听 8080；对外端口由 compose 的 ports 映射决定
HEALTHCHECK --interval=15s --timeout=5s --start-period=10s --retries=4 \
    CMD python -c "import urllib.request,sys; \
sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/healthz', timeout=4).status==200 else 1)"

ENTRYPOINT ["python", "-m", "app.main"]
CMD ["/config/dyn2xyz.toml"]
