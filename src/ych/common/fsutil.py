# 文件安全工具：Windows 长路径兼容、原子写入、只读保护（详设 2.2 / 10.2）
from __future__ import annotations

import contextlib
import os
import stat
from collections.abc import Callable
from pathlib import Path

# 超过该长度自动加 \\?\ 前缀（Windows 长路径兼容）
_LONG_PATH_THRESHOLD = 240


def long_path(path: Path) -> Path:
    """超过 240 字符的路径加 \\\\?\\ 前缀，否则原样返回。"""
    s = str(path)
    if len(s) > _LONG_PATH_THRESHOLD and not s.startswith("\\\\?\\"):
        return Path("\\\\?\\" + os.path.abspath(s))
    return path


class SafeFileOps:
    """非破坏性文件操作静态工具（原始素材只读保护原则）。"""

    @staticmethod
    def atomic_write(target: Path, writer: Callable[[Path], None]) -> None:
        """临时文件写入 + fsync + 同卷原子重命名。

        writer(tmp_path) 负责把全部内容写入临时文件；
        writer 返回后强制 fsync 再原子替换；writer 抛异常时
        清理残留，target 保持不变。
        """
        tmp = target.with_suffix(target.suffix + ".part.tmp")
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            writer(tmp)
            # 强制落盘后原子替换（os.replace 同卷原子）；Windows 下 fsync
            # 需要可写句柄，故以 rb+ 打开
            with open(long_path(tmp), "rb+") as f:
                os.fsync(f.fileno())
            os.replace(long_path(tmp), long_path(target))
        except BaseException:
            # 清理半成品；target 不受影响
            if tmp.exists():
                SafeFileOps.safe_delete(tmp)
            raise

    @staticmethod
    def protect_readonly(path: Path, readonly: bool) -> None:
        """设置/解除 Windows 只读属性。"""
        p = long_path(Path(path))
        mode = stat.S_IREAD if readonly else (stat.S_IREAD | stat.S_IWRITE)
        os.chmod(p, mode)

    @staticmethod
    def safe_delete(path: Path) -> None:
        """安全删除：存在才删；只读先解锁；目录递归删除。"""
        p = long_path(Path(path))
        if not p.exists():
            return
        if p.is_dir():
            _rmtree(p)
        else:
            SafeFileOps._ensure_writable(p)
            p.unlink()

    @staticmethod
    def _ensure_writable(path: Path) -> None:
        """删除前解除只读位。"""
        # 个别系统文件可能拒绝改权限；删除动作本身稍后会再抛出真实错误
        with contextlib.suppress(PermissionError):
            os.chmod(long_path(path), stat.S_IREAD | stat.S_IWRITE)


def _rmtree(directory: Path) -> None:
    """自实现递归删除（避免 shutil 权限问题）。"""
    for child in directory.iterdir():
        if child.is_dir():
            _rmtree(child)
        else:
            SafeFileOps._ensure_writable(child)
            child.unlink()
    directory.rmdir()
