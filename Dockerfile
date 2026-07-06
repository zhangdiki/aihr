FROM python:3.11-slim

WORKDIR /app

# 系统依赖（ffmpeg 用于音频转换）
RUN apt-get update && apt-get install -y ffmpeg && rm -rf /var/lib/apt/lists/*

# Python 依赖
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 应用代码
COPY . .

ENV DATABASE_PATH=/app/data/data.db

# 如需持久化 SQLite，在 Railway 后台挂载 Volume 到 /app/data
RUN mkdir -p /app/data

EXPOSE 8000

CMD exec uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000}
