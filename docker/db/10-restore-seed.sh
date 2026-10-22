#!/bin/sh
# db 容器第一次啟動（資料 volume 是空的）時由 postgres 官方 image 自動執行。
# 有 seed 就還原 chunks 資料表；沒有就只建 vector extension，之後可以用 scripts/ingest.py 寫入資料。
set -e
psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "CREATE EXTENSION IF NOT EXISTS vector;"
if [ -f /seed/chunks.dump ]; then
  echo "還原 seed：/seed/chunks.dump"
  # --no-owner：dump 是用 twse 使用者產生的，新環境的 POSTGRES_USER 可能不同
  pg_restore --no-owner --no-privileges -U "$POSTGRES_USER" -d "$POSTGRES_DB" /seed/chunks.dump
  psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -tAc "SELECT 'chunks: ' || count(*) FROM chunks;"
else
  echo "找不到 /seed/chunks.dump：資料庫是空的。請先執行 scripts/fetch_seed.sh，再刪掉 db volume 重新啟動（見 README）。"
fi
