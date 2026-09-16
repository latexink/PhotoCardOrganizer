"""On-demand logical library size, without reading media contents."""

import os
from pathlib import Path

from .organizer import Organizer


def library_size(root: Path) -> int:
    root = Path(root)
    if not root.is_dir() or Organizer._is_link_like(root):
        raise OSError("Library is unavailable or is a link")
    total = 0
    pending = [root]
    while pending:
        with os.scandir(pending.pop()) as entries:
            for entry in entries:
                path = Path(entry.path)
                if Organizer._is_link_like(path):
                    continue
                if entry.is_dir(follow_symlinks=False):
                    pending.append(path)
                elif entry.is_file(follow_symlinks=False):
                    total += entry.stat(follow_symlinks=False).st_size
    return total
