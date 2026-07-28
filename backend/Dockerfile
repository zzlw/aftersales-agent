FROM python:3.12-slim

# 引入 uv 二进制（官方分发镜像，固定版本 tag 保证构建可复现，与本机 uv 0.11 对齐）
COPY --from=ghcr.io/astral-sh/uv:0.11 /uv /bin/uv

WORKDIR /code

# UV_NO_CACHE：等价 pip --no-cache-dir，不在镜像层留缓存
# UV_PYTHON_DOWNLOADS=never：钉死用基础镜像自带的 3.12，禁止 uv 自行下载解释器
# UV_DEFAULT_INDEX：国内加速（替代 pip 的 -i 参数）
ENV UV_NO_CACHE=1 \
    UV_PYTHON_DOWNLOADS=never \
    UV_DEFAULT_INDEX=https://pypi.tuna.tsinghua.edu.cn/simple

# 依赖层单独缓存：先拷清单，再 sync（--frozen 严格按 uv.lock 安装）
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# 关键：把 .venv/bin 挂上 PATH——docker-compose.yml 的 command 会覆盖 CMD，裸调 uvicorn 靠它
ENV PATH="/code/.venv/bin:$PATH"

COPY app ./app

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
