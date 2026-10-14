"""Private user directories for authenticated resource leases."""
import os
import stat
from pathlib import Path

def private_directory(path: Path) -> None:
    path.mkdir(parents=True,exist_ok=True,mode=0o700)
    info=path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid!=os.geteuid():
        raise OSError('资源目录不是当前用户拥有的普通目录')
    path.chmod(0o700)
