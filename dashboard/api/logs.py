"""Log file viewer — read-only access to trading system logs."""
import os
import subprocess
from pathlib import Path

from fastapi import APIRouter, HTTPException

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
LOG_DIR = REPO_ROOT / "src" / "logs"

router = APIRouter()

# ── Runtime error capture (for browser JS crashes) ───────────
_dashboard_errors = []

@router.get("/api/debug/errors")
def get_runtime_errors():
    """Return JavaScript console errors captured from the browser."""
    return {"errors": list(_dashboard_errors)}


@router.get("/api/logs/list")
def get_log_files():
    """Return list of available .log files with sizes."""
    if not LOG_DIR.exists():
        raise HTTPException(status_code=503, detail="Log directory not found")

    files = []
    for f in sorted(LOG_DIR.glob("*.log")):
        stat = f.stat()
        files.append({
            "name": f.name,
            "size_bytes": stat.st_size,
            "size_kb": round(stat.st_size / 1024, 1),
            "mtime": stat.st_mtime,
        })

    return {"files": files}


@router.get("/api/logs/last")
def get_log_lines(file: str = "crypto_engine.log", lines: int = 100):
    """Return the last N lines of a log file via tail -n."""
    if not LOG_DIR.exists():
        raise HTTPException(status_code=503, detail="Log directory not found")

    # Safety: only allow known log filenames, no path traversal
    safe_name = Path(file).name  # strips any path components
    if ".." in file or "/" in file and file != safe_name:
        raise HTTPException(status_code=400, detail="Invalid filename")

    log_path = LOG_DIR / safe_name
    if not log_path.exists():
        raise HTTPException(status_code=404, detail=f"Log file not found: {safe_name}")

    if not lines or lines < 1:
        lines = 100
    if lines > 2000:
        lines = 2000  # cap to prevent abuse

    try:
        result = subprocess.run(
            ["tail", "-n", str(lines), str(log_path)],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode != 0:
            raise HTTPException(status_code=500, detail=f"tail failed: {result.stderr}")

        # Split into lines, remove empty trailing line if any
        text_lines = result.stdout.rstrip("\n").split("\n") if result.stdout.strip() else []
        if not text_lines or (len(text_lines) == 1 and text_lines[0] == ""):
            text_lines = []

        return {
            "file": safe_name,
            "total_lines_in_file": _count_lines(log_path),
            "returned_lines": len(text_lines),
            "lines": text_lines,
            "size_kb": round(log_path.stat().st_size / 1024, 1),
        }
    except subprocess.TimeoutExpired:
        raise HTTPException(status_code=504, detail="tail timed out")


def _count_lines(path):
    """Count lines in a file (used for display only)."""
    try:
        result = subprocess.run(["wc", "-l", str(path)], capture_output=True, text=True, timeout=5)
        return int(result.stdout.split()[0]) if result.returncode == 0 else 0
    except Exception:
        return 0
