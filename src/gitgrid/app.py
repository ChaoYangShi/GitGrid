import os
import subprocess
from concurrent.futures import ThreadPoolExecutor

from rich.markup import escape
from textual.app import App, ComposeResult
from textual.screen import ModalScreen
from textual.containers import VerticalScroll
from textual.widgets import (
    DataTable, Header, Footer, Static, ListView, ListItem, Label,
)
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


def _run_cmd(cmd: list, cwd: str, strip: bool = True) -> str:
    """运行 git 命令并返回 stdout（出错返回空串）

    strip=False 用于 porcelain -z 等输出：其首条记录可能以空格开头
    （表示状态码），strip 会吃掉前导空格导致解析错位。
    """
    try:
        result = subprocess.run(
            cmd, cwd=cwd, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, text=True,
        )
        return result.stdout.strip() if strip else result.stdout
    except Exception:
        return ""


def _colorize_diff(text: str) -> str:
    """给 git diff 文本逐行上色（转义后再加 Rich 标记，避免内容里的 [] 被误解析）"""
    out = []
    for line in text.splitlines():
        safe = escape(line)
        if line.startswith("+++") or line.startswith("---"):
            out.append(f"[bold]{safe}[/bold]")
        elif line.startswith("@@"):
            out.append(f"[cyan]{safe}[/cyan]")
        elif line.startswith("+"):
            out.append(f"[green]{safe}[/green]")
        elif line.startswith("-"):
            out.append(f"[red]{safe}[/red]")
        elif line.startswith("diff ") or line.startswith("index "):
            out.append(f"[dim]{safe}[/dim]")
        else:
            out.append(safe)
    return "\n".join(out)


class DiffScreen(ModalScreen):
    """展示单个文件的 git diff"""

    CSS = """
    DiffScreen {
        align: center middle;
    }
    #diff-box {
        width: 92%;
        height: 90%;
        border: thick cyan;
        background: $surface;
        padding: 1 2;
    }
    """

    BINDINGS = [
        Binding("escape", "dismiss", "Back"),
        Binding("q", "dismiss", "Back"),
    ]

    def __init__(self, repo_path: str, file_path: str, staged: bool, untracked: bool) -> None:
        super().__init__()
        self.repo_path = repo_path
        self.file_path = file_path
        self.staged = staged
        self.untracked = untracked

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="diff-box"):
            yield Static(self._build_diff())

    def _build_diff(self) -> str:
        header = f"[bold cyan]{escape(self.file_path)}[/bold cyan]\n\n"
        if self.untracked:
            # 未追踪文件没有 HEAD 版本，用 --no-index 对比 /dev/null 显示全文
            diff = _run_cmd(
                ["git", "diff", "--no-index", "--", os.devnull, self.file_path],
                self.repo_path,
            )
            body = _colorize_diff(diff) if diff else "[dim]no diff[/dim]"
            return header + body + "\n\n[dim]Press Esc / q to go back[/dim]"

        # 追踪文件可能同时存在已暂存与未暂存改动（porcelain 的 MM），
        # 分别取两段，避免只显示一半。
        staged = _run_cmd(["git", "diff", "--cached", "--", self.file_path], self.repo_path)
        unstaged = _run_cmd(["git", "diff", "--", self.file_path], self.repo_path)

        sections = []
        if staged:
            sections.append("[bold green]▼ Staged changes[/bold green]\n" + _colorize_diff(staged))
        if unstaged:
            sections.append("[bold yellow]▼ Unstaged changes[/bold yellow]\n" + _colorize_diff(unstaged))
        if not sections:
            sections.append("[dim]no diff[/dim]")

        body = "\n\n".join(sections)
        return header + body + "\n\n[dim]Press Esc / q to go back[/dim]"


class RepoDetailScreen(ModalScreen):
    """单个仓库的详情弹窗：可点击的修改文件 / 远端提交 / 本地提交 / stash"""

    CSS = """
    RepoDetailScreen {
        align: center middle;
    }
    #detail-box {
        width: 90%;
        height: 90%;
        border: thick green;
        background: $surface;
        padding: 1 2;
    }
    #detail-box Static {
        margin-bottom: 1;
    }
    #file-list {
        height: auto;
        max-height: 12;
        border: round $accent;
        margin-bottom: 1;
    }
    """

    BINDINGS = [
        Binding("escape", "dismiss", "Back"),
        Binding("q", "dismiss", "Back"),
    ]

    def __init__(self, path: str) -> None:
        super().__init__()
        self.repo_path = path
        # ListItem -> (相对文件路径, 是否暂存, 是否未追踪)
        self.file_index: dict = {}

    def compose(self) -> ComposeResult:
        path = self.repo_path
        with VerticalScroll(id="detail-box"):
            yield Static(f"[bold green]{escape(path)}[/bold green]")

            branch = _run_cmd(["git", "branch", "--show-current"], path) or "(detached)"
            upstream = _run_cmd(["git", "rev-parse", "--abbrev-ref", "@{u}"], path)
            has_upstream = bool(upstream) and upstream != "@{u}"
            yield Static(
                f"[bold]Branch:[/bold] {escape(branch)}"
                + (f"  →  {escape(upstream)}" if has_upstream else "  (no upstream)")
            )

            # 1. 修改文件：做成可点击列表，点击进入 diff
            yield Static("[bold yellow]── Changed files (click to diff) ──[/bold yellow]")
            yield from self._compose_file_list()

            # 2. 远端提交（待 pull），详细格式：作者 / 时间 / 文件
            yield Static("[bold cyan]── Incoming commits (remote) ──[/bold cyan]")
            yield Static(self._incoming_commits(has_upstream, upstream))

            # 3. 本地提交（待 push）
            yield Static("[bold magenta]── Outgoing commits (local) ──[/bold magenta]")
            yield Static(self._outgoing_commits(has_upstream, upstream))

            # 4. Stash
            yield Static("[bold blue]── Stash ──[/bold blue]")
            stash = _run_cmd(["git", "stash", "list"], path)
            yield Static(escape(stash) if stash else "[dim]none[/dim]")

            yield Static("[dim]Enter/click a file to view diff · Esc/q to go back[/dim]")

    def _compose_file_list(self):
        """根据 git status --porcelain -z 生成可点击的文件列表

        用 -z（NUL 分隔）而非默认格式：默认格式会对含空格/中文/特殊字符的
        文件名加引号并做八进制转义，导致解析出的路径无法被 git diff 匹配、
        diff 显示为空。-z 保持文件名原样。
        """
        path = self.repo_path
        status = _run_cmd(["git", "status", "--porcelain", "-z"], path, strip=False)
        if not status:
            yield Static("[dim]clean[/dim]")
            return

        # -z 输出以 NUL 分隔；重命名/复制（状态含 R/C）会额外占用一个
        # NUL 段存放旧路径，需要跳过它。
        parts = status.split("\x00")
        items = []
        i = 0
        while i < len(parts):
            entry = parts[i]
            if not entry:
                i += 1
                continue
            xy = entry[:2]
            fname = entry[3:]
            # 重命名/复制：下一段是旧路径，跳过；当前 fname 已是新路径
            if xy[0] in ("R", "C"):
                i += 1
            untracked = xy == "??"
            staged = (not untracked) and xy[0] != " "
            label = f"{xy} {fname}"
            item = ListItem(Label(escape(label)))
            items.append(item)
            self.file_index[item] = (fname, staged, untracked)
            i += 1

        yield ListView(*items, id="file-list")

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        """点击/回车选中某个文件，弹出其 diff"""
        info = self.file_index.get(event.item)
        if info:
            fname, staged, untracked = info
            self.app.push_screen(DiffScreen(self.repo_path, fname, staged, untracked))

    def _incoming_commits(self, has_upstream: bool, upstream: str) -> str:
        if not has_upstream:
            return "[dim]no upstream[/dim]"
        return self._detailed_log(f"HEAD..{upstream}")

    def _outgoing_commits(self, has_upstream: bool, upstream: str) -> str:
        if has_upstream:
            return self._detailed_log(f"{upstream}..HEAD")
        # 无 upstream 时退而展示最近 5 条本地提交
        return self._detailed_log("-5")

    def _detailed_log(self, rev_range: str) -> str:
        """带作者、时间、变更文件的提交日志"""
        path = self.repo_path
        # 用 %x1f(单元分隔) 和 %x1e(记录分隔) 包裹各字段，便于解析
        # 记录分隔符 %x1e 放在每条记录开头：这样 --name-only 输出的文件
        # 会紧跟在本条 header 之后、下一个 %x1e 之前，归属正确。
        fmt = "%x1e%h%x1f%an%x1f%ad%x1f%s"
        cmd = ["git", "log", f"--pretty=format:{fmt}",
               "--date=format:%Y-%m-%d %H:%M", "--name-only", rev_range]
        raw = _run_cmd(cmd, path)
        if not raw:
            return "[dim]none[/dim]"

        blocks = [b for b in raw.split("\x1e") if b.strip()]
        out = []
        for block in blocks:
            # block 形如: "<header>\n<file>\n<file>..."；header 含 \x1f 分隔字段
            header, _, files_part = block.strip().partition("\n")
            fields = header.split("\x1f")
            if len(fields) < 4:
                continue
            sha, author, date, subject = fields[0], fields[1], fields[2], fields[3]
            out.append(
                f"[bold yellow]{escape(sha)}[/bold yellow] {escape(subject)}\n"
                f"    [dim]{escape(author)} · {escape(date)}[/dim]"
            )
            files = [f for f in files_part.splitlines() if f.strip()]
            for f in files:
                out.append(f"    [green]•[/green] {escape(f)}")
            out.append("")
        return "\n".join(out).rstrip() if out else "[dim]none[/dim]"


class MultiRepoGitApp(App):
    """一个管理当前目录及所有子目录下 Git 仓库的 TUI 工具"""

    TITLE = "Multi-Repo Git Status"
    CSS = """
    DataTable {
        height: 100%;
        border: solid green;
    }
    """

    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("r", "refresh", "Refresh"),
        Binding("f", "toggle_fetch", "Toggle Fetch"),
    ]

    # 是否在刷新时先执行 git fetch 更新远程跟踪分支
    # 关闭后只看本地缓存的远端状态（速度快，但可能过期）
    auto_fetch = True

    def __init__(self) -> None:
        super().__init__()
        # 表格行 key -> 仓库绝对路径，供点进详情时定位
        self.row_paths: dict = {}

    def compose(self) -> ComposeResult:
        yield Header()
        yield DataTable(id="repo-table")
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one(DataTable)
        table.cursor_type = "row"
        table.zebra_stripes = True

        table.add_columns(
            "Path",
            "Branch",
            "Remote",
            "Staged",
            "Modified",
            "Untracked",
            "Stash"
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
                setattr, self, "sub_title", f"Fetching {total} repos…"
            )
            # 并行 fetch，避免逐个网络往返把整体拖慢
            with ThreadPoolExecutor(max_workers=8) as pool:
                pool.map(self.git_fetch, repo_paths)

        self.call_from_thread(
            setattr, self, "sub_title",
            f"{total} repos | Fetch: {'on' if self.auto_fetch else 'off'}"
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
        self.row_paths.clear()
        for status in results:
            row_key = table.add_row(
                status["name"],
                status["branch"],
                status["remote"],
                status["staged"],
                status["modified"],
                status["untracked"],
                status["stash"]
            )
            # 记录该行对应的仓库路径，供回车/点击查看详情
            self.row_paths[row_key] = status["path"]

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        """选中某行（回车或点击）时，弹出该仓库的详情"""
        path = self.row_paths.get(event.row_key)
        if path:
            self.push_screen(RepoDetailScreen(path))

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
        remote_status = "✔️ synced"
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
            "path": path,
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
