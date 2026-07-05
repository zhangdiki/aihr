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

# 创建数据目录（SQLite 持久化点）
RUN mkdir -p /app/data
VOLUME ["/app/data"]

EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
