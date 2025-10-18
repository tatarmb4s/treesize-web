import argparse
import logging
import os
import sys
from typing import List

try:
    from systemd.journal import JournalHandler
    _HAS_JOURNAL = True
except Exception:
    _HAS_JOURNAL = False


def configure_logging() -> logging.Logger:
    """Configure journald or stderr logging for the helper."""
    logger = logging.getLogger("treesize-delete-helper")
    logger.setLevel(logging.INFO)
    if _HAS_JOURNAL:
        handler: logging.Handler = JournalHandler()
    else:
        handler = logging.StreamHandler()
    logger.addHandler(handler)
    return logger


def normalize(path: str) -> str:
    """Resolve real absolute path."""
    return os.path.realpath(os.path.abspath(path))


def ensure_within_base(base: str, target: str) -> None:
    """Raise if target is not inside base after realpath resolution."""
    base_real = normalize(base)
    target_real = normalize(target)
    common = os.path.commonpath([base_real, target_real])
    if common != base_real:
        raise ValueError("target_not_under_base")


def secure_delete(path: str) -> None:
    """Delete a file tree without following symlinks."""
    st = os.lstat(path)
    if not os.path.isdir(path) or os.path.islink(path):
        os.unlink(path)
        return
    stack: List[str] = [path]
    seen: List[str] = []
    while stack:
        current = stack.pop()
        try:
            with os.scandir(current) as it:
                for entry in it:
                    p = entry.path
                    if entry.is_symlink():
                        os.unlink(p)
                        continue
                    if entry.is_dir(follow_symlinks=False):
                        stack.append(p)
                        continue
                    os.unlink(p)
            seen.append(current)
        except FileNotFoundError:
            continue
    for d in reversed(seen):
        try:
            os.rmdir(d)
        except FileNotFoundError:
            continue


def main() -> int:
    """Entry point for privileged deletions."""
    logger = configure_logging()
    parser = argparse.ArgumentParser(prog="treesize-delete-helper")
    parser.add_argument("--base", required=True)
    parser.add_argument("--path", required=True)
    args = parser.parse_args()
    base = normalize(args.base)
    target = normalize(args.path)
    try:
        ensure_within_base(base, target)
        if target == base:
            raise ValueError("refuse_delete_base")
        secure_delete(target)
        logger.info("deleted base=%s target=%s uid=%s", base, target, os.geteuid())
        return 0
    except Exception as exc:  # noqa: BLE001
        logger.error("delete_failed base=%s target=%s error=%s", base, target, str(exc))
        return 1


if __name__ == "__main__":
    sys.exit(main())


