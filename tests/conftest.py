import os

import pytest


def skip_or_fail_without_db(reason: str = "連不上測試資料庫") -> None:
    """本機沒開資料庫時跳過整合測試；CI 設 REQUIRE_DB_TESTS=1，連不上就直接失敗，避免測試被悄悄跳過。"""
    if os.environ.get("REQUIRE_DB_TESTS") == "1":
        pytest.fail(f"{reason}（REQUIRE_DB_TESTS=1）")
    pytest.skip(reason)
