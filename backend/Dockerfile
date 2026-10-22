# syntax=docker/dockerfile:1
# build context：專案根目錄（docker-compose.yml 裡設定），因為後端也用到 common/ 與 config/

# --- 建置階段：在 venv 裡裝好套件，執行階段只複製 venv，不帶 pip 快取與編譯工具 ---
FROM python:3.13-slim AS build
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
RUN python -m venv /venv
COPY backend/requirements.txt /tmp/requirements.txt
# 縮小 image：pytest 只在開發用；uvicorn[standard] 的 uvloop、watchfiles 等是給 --reload 與高併發用的，
# 本機 demo 用純 Python 版就夠（省約 25 MB）；裝完再移除 venv 裡的 pip
RUN grep -v '^pytest' /tmp/requirements.txt | sed 's/uvicorn\[standard\]/uvicorn/' > /tmp/requirements-prod.txt \
 && /venv/bin/pip install -r /tmp/requirements-prod.txt \
 && /venv/bin/pip uninstall -y pip

# --- 執行階段 ---
FROM python:3.13-slim
ENV PATH=/venv/bin:$PATH PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
RUN useradd --create-home --uid 1000 app
WORKDIR /app
COPY --from=build /venv /venv
COPY common/ common/
COPY config/ config/
COPY backend/ backend/
USER app
EXPOSE 8000
HEALTHCHECK --interval=10s --timeout=3s --retries=5 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health', timeout=2)"
CMD ["uvicorn", "backend.app.main:app", "--host", "0.0.0.0", "--port", "8000"]
