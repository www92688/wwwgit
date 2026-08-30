# LogService 单元测试（sanitize 表驱动）
import logging

import pytest

from ych.services.s5_base.log_service import LogService


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("https://api.x.com/v1?key=abc123", "https://api.x.com/v1?key=***"),
        ("GET https://p.cn/s?q=1&token=tok-9", "GET https://p.cn/s?q=1&token=***"),
        ("KEY=upper&token=zz", "KEY=***&token=***"),
        ("no secrets here", "no secrets here"),
        ("a=1?key=v&b=2", "a=1?key=***&b=2"),
    ],
)
def test_sanitize_table(raw: str, expected: str) -> None:
    assert LogService.sanitize(raw) == expected
def test_setup_creates_rotating_log(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    LogService.setup("DEBUG")
    logging.getLogger("ych.s5.test").debug("hello log")
    for h in logging.getLogger().handlers:
        h.flush()
    log_file = tmp_path / "logs" / "app.log"
    assert log_file.exists()
    assert "hello log" in log_file.read_text(encoding="utf-8")

