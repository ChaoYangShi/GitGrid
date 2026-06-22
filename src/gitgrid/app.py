import os
import subprocess
from concurrent.futures import ThreadPoolExecutor

from textual.app import App, ComposeResult
from textual.widgets import DataTable, Header, Footer
from textual.binding import Binding
from textual import work

# ==========================================
# 递归时忽略这些深层、编译生成或第三方目录，
# 既提升扫描性能，也避免展示无关的仓库状态。
# ==========================================
IGNORE_DIRS = {
    '.git', '__pycache__', 'venv', '.venv', 'env',
    'build', 'devel', 'install', 'log', 'node_modules',
    # 第三方依赖目录，不关心其 repo 状态
    'thirdparty', 'third_party', '3rdparty', 'vendor',
}


class MultiRepoGitApp(App):
    """一个管理当前目录及所有子目录下 Git 仓库的 TUI 工具"""

    TITLE = "Multi-Repo Git Status (递归版)"
    CSS = """
    DataTable {
        height: 100%;
        border: solid green;
    }
    """

    BINDINGS = [
        Binding("q", "quit", "退出"),
        Binding("r", "refresh", "刷新状态"),
        Binding("f", "toggle_fetch", "开关 Fetch"),
    ]

    # 是否在刷新时先执行 git fetch 更新远程跟踪分支
    # 关闭后只看本地缓存的远端状态（速度快，但可能过期）
    auto_fetch = True

    def compose(self) -> ComposeResult:
        yield Header()
        yield DataTable(id="repo-table")
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one(DataTable)
        table.cursor_type = "row"
        table.zebra_stripes = True

        table.add_columns(
            "仓库路径 (Path)",
            "分支 (Branch)",
            "远端 (Remote)",
            "暂存 (Staged)",
            "修改 (Modified)",
            "未追踪 (Untracked)",
            "储藏 (Stash)"
        )
        self.action_refresh()

    def action_toggle_fetch(self) -> None:
        """切换刷新时是否执行 git fetch"""
        self.auto_fetch = not self.auto_fetch
        self.action_refresh()

    def action_refresh(self) -> None:
        # 把耗时的扫描 + fetch 放到后台 worker，避免阻塞 UI
        self.refresh_worker()

    @work(thread=True, exclusive=True)
    def refresh_worker(self) -> None:
        base_dir = os.getcwd()

        repo_paths = []
        # 递归遍历目录树
        for root, dirs, files in os.walk(base_dir):
            # 动态剪枝：原地修改 dirs 列表，阻止 os.walk 进入被忽略的目录
            dirs[:] = [d for d in dirs if d not in IGNORE_DIRS]

            # 判断当前 root 是否是一个 git 仓库 (检查是否存在 .git 目录或文件)
            if os.path.exists(os.path.join(root, '.git')):
                repo_paths.append(root)

        repo_paths.sort()
        total = len(repo_paths)

        if self.auto_fetch and total:
            self.call_from_thread(
                setattr, self, "sub_title", f"正在 fetch {total} 个仓库…"
            )
            # 并行 fetch，避免逐个网络往返把整体拖慢
            with ThreadPoolExecutor(max_workers=8) as pool:
                pool.map(self.git_fetch, repo_paths)

        self.call_from_thread(
            setattr, self, "sub_title",
            f"{total} 个仓库 | Fetch: {'开' if self.auto_fetch else '关'}"
        )

        # 并行收集各仓库状态
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(
                lambda p: self.get_git_status(
                    p,
                    os.path.basename(base_dir)
                    if os.path.relpath(p, base_dir) == "."
                    else os.path.relpath(p, base_dir),
                ),
                repo_paths,
            ))

        self.call_from_thread(self.populate_table, results)

    def populate_table(self, results: list) -> None:
        """在主线程中刷新表格内容"""
        table = self.query_one(DataTable)
        table.clear()
        for status in results:
            table.add_row(
                status["name"],
                status["branch"],
                status["remote"],
                status["staged"],
                status["modified"],
                status["untracked"],
                status["stash"]
            )

    def git_fetch(self, path: str) -> None:
        """更新远程跟踪分支，加超时防止远端无响应时挂死整个刷新"""
        try:
            subprocess.run(
                ["git", "fetch", "--quiet"],
                cwd=path,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=20,
            )
        except Exception:
            pass

    def run_cmd(self, cmd: list, cwd: str) -> str:
        try:
            result = subprocess.run(cmd, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
            return result.stdout.strip()
        except Exception:
            return ""

    def get_git_status(self, path: str, name: str) -> dict:
        # 1. 分支名
        branch = self.run_cmd(["git", "branch", "--show-current"], cwd=path)
        if not branch:
            branch = self.run_cmd(["git", "rev-parse", "--short", "HEAD"], cwd=path)
            branch = f"({branch})"

        # 2. 远端状态 (Ahead/Behind)
        remote_status = "✔️ 同步"
        behind_count = 0
        upstream = self.run_cmd(["git", "rev-parse", "--abbrev-ref", "@{u}"], cwd=path)
        if upstream and upstream != "@{u}":
            counts = self.run_cmd(["git", "rev-list", "--left-right", "--count", f"HEAD...{upstream}"], cwd=path)
            if counts:
                ahead, behind = counts.split()
                behind_count = int(behind)
                if ahead != "0" or behind != "0":
                    remote_status = f"⬆️ {ahead} ⬇️ {behind}"

        # 3. 本地变动统计
        staged = self.run_cmd(["git", "diff", "--name-only", "--cached"], cwd=path)
        staged_count = len(staged.splitlines()) if staged else 0

        modified = self.run_cmd(["git", "diff", "--name-only"], cwd=path)
        modified_count = len(modified.splitlines()) if modified else 0

        untracked = self.run_cmd(["git", "ls-files", "--others", "--exclude-standard"], cwd=path)
        untracked_count = len(untracked.splitlines()) if untracked else 0

        # 4. Stash 统计
        stash = self.run_cmd(["git", "stash", "list"], cwd=path)
        stash_count = len(stash.splitlines()) if stash else 0

        # 有远程更新（behind > 0）时，整行用醒目颜色高亮，方便一眼定位
        if behind_count > 0:
            name_cell = f"[bold red]🔴 {name}[/bold red]"
            remote_cell = f"[bold red]{remote_status}[/bold red]"
        else:
            name_cell = f"📁 {name}"
            remote_cell = remote_status

        return {
            "name": name_cell,
            "branch": f"🌱 {branch}",
            "remote": remote_cell,
            "staged": f"[green]{staged_count}[/green]" if staged_count > 0 else "0",
            "modified": f"[red]{modified_count}[/red]" if modified_count > 0 else "0",
            "untracked": f"[yellow]{untracked_count}[/yellow]" if untracked_count > 0 else "0",
            "stash": f"[magenta]{stash_count}[/magenta]" if stash_count > 0 else "0",
        }


def main() -> None:
    """命令行入口：gitgrid"""
    app = MultiRepoGitApp()
    app.run()


if __name__ == "__main__":
    main()
