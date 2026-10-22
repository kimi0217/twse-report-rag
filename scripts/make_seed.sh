#!/bin/sh
# 維護者用：從本機資料庫匯出 chunks 資料表當作 seed，上傳到 GitHub Release 給其他人用 fetch_seed.sh 下載。
# 用法：PG_CONTAINER=twse-pg sh scripts/make_seed.sh
set -e
cd "$(dirname "$0")/.."
CONTAINER="${PG_CONTAINER:-twse-pg}"
USER="${POSTGRES_USER:-twse}"
DB="${POSTGRES_DB:-twse}"
mkdir -p data/seed
# -Fc：壓縮格式，pg_restore 還原時重建 HNSW 索引（索引本身不放進 dump，檔案小一半）
docker exec "$CONTAINER" pg_dump -U "$USER" -d "$DB" -t chunks -Fc -Z 9 --no-owner > data/seed/chunks.dump
if command -v sha256sum >/dev/null 2>&1; then SHA="sha256sum"; else SHA="shasum -a 256"; fi
$SHA data/seed/chunks.dump | awk '{print $1 "  chunks.dump"}' > data/seed/chunks.dump.sha256
echo "data/seed/chunks.dump：$(wc -c < data/seed/chunks.dump | tr -d ' ') bytes"
echo "下一步：上傳到 GitHub Release，並 commit data/seed/chunks.dump.sha256"
