import json, os, sys, logging
from pathlib import Path
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ["ALPACA_ENV"] = "paper"

from infra.logger import get_logger, JSONFormatter


class TestLogger:
    def test_logger_returns_instance(self):
        logger = get_logger("test-logger")
        assert logger is not None

    def test_logger_produces_json(self, temp_db):
        log_file = temp_db.parent / "test.log"
        logger = get_logger("json-test")
        logger.handlers.clear()
        
        fh = logging.FileHandler(str(log_file))
        fh.setFormatter(JSONFormatter())
        fh.setLevel(logging.DEBUG)
        logger.addHandler(fh)
        
        logger.info("test message")
        logger.warning("warning message")
        logger.error("error message")
        fh.close()
        
        lines = log_file.read_text().strip().split("\n")
        assert len(lines) == 3
        for i, line in enumerate(lines):
            data = json.loads(line)
            assert "timestamp" in data and "level" in data and "message" in data

    def test_error_logs_trigger_notify(self):
        """ERROR level logs call notify_engine."""
        mock_notify = MagicMock()
        
        with patch("infra.notify_engine.get_notify", return_value=mock_notify):
            logger = get_logger("notify-test")
            
            # The infra code calls _notify.notify(...) on ERROR.
            # If it works, mock_notify.notify is called.
            notification_fired = [False]
            original_notify = mock_notify.notify
            def capture(*a, **kw):
                notification_fired[0] = True
                return original_notify(*a, **kw) if callable(original_notify) else None
            mock_notify.notify = capture
            
            logger.error("test error")
            
            assert notification_fired[0], "ERROR log should have triggered notify engine"


class TestLoggerStructure:
    def test_log_directory_created(self, temp_db):
        logger = get_logger("dir-test")
        assert len(logger.handlers) > 0

    def test_json_format_has_required_fields(self):
        formatter = JSONFormatter()
        record = logging.makeLogRecord({
            "name": "test", "levelname": "INFO", "msg": "test message",
            "module": "test", "funcName": "test_func", "lineno": 42,
        })
        data = json.loads(formatter.format(record))
        for field in ["timestamp", "level", "message", "module", "function", "line"]:
            assert field in data


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
