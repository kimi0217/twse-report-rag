#!/bin/sh
# 下載預先建好的資料庫 seed（5 家公司 2025 年報的內文與表格 chunk，含 bge-m3 embedding）。
# db 容器第一次啟動時會自動還原它，不需要 PDF、也不需要跑 ingestion。
set -e
cd "$(dirname "$0")/.."
TAG="${SEED_TAG:-seed-2025-v1}"
URL="${SEED_URL:-https://github.com/kimi0217/twse-report-rag/releases/download/$TAG/chunks.dump}"
mkdir -p data/seed
echo "下載 $URL"
curl -fL --retry 3 -o data/seed/chunks.dump "$URL"
# 檢查檔案完整，避免下載到一半或錯的版本。Linux 多半只有 sha256sum，macOS 只有 shasum
if command -v sha256sum >/dev/null 2>&1; then
  (cd data/seed && sha256sum -c chunks.dump.sha256)
else
  (cd data/seed && shasum -a 256 -c chunks.dump.sha256)
fi
echo "完成：data/seed/chunks.dump"
