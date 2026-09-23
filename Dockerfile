# syntax=docker/dockerfile:1

########## 前端构建 ##########
FROM node:20-bookworm-slim AS frontend-build
WORKDIR /frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

########## 运行时：后端托管前端产物的合并部署 ##########
FROM python:3.12-slim-bookworm AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY backend/requirements.txt backend/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt
COPY backend/ backend/
COPY --from=frontend-build /frontend/dist frontend/dist
WORKDIR /app/backend
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]

########## 一次性校验：代码测试 + 构建检查 + API 冒烟 ##########
FROM python:3.12-bookworm AS verify
# 从官方 Node 镜像拷贝 node/npm，用于前端构建检查。
COPY --from=node:20-bookworm-slim /usr/local /usr/local
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY backend/requirements.txt backend/requirements-dev.txt backend/
RUN pip install --no-cache-dir -r backend/requirements-dev.txt
COPY backend/ backend/
COPY frontend/ frontend/
COPY --from=frontend-build /frontend/node_modules frontend/node_modules
COPY scripts/ scripts/
RUN chmod +x scripts/verify.sh
CMD ["bash", "scripts/verify.sh"]
