"""GitGrid: 递归管理当前目录下所有 Git 子仓库状态的 TUI 工具。"""

from gitgrid.app import MultiRepoGitApp, main

__version__ = "0.1.0"
__all__ = ["MultiRepoGitApp", "main", "__version__"]
