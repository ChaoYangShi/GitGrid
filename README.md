# GitGrid

递归扫描当前目录下所有 Git 子仓库，在一个终端表格里集中展示它们的分支、远端同步状态、本地改动和储藏；选中任一仓库可深入查看变更文件 diff、待 pull/push 的提交详情。

## 特性

- 递归发现子仓库，自动忽略 `thirdparty` / `vendor` / `node_modules` / `build` 等无关目录
- 刷新时自动 `git fetch`（并行 + 超时），能真实检测到**远程有更新**的仓库
- 有远程更新（behind > 0）的仓库整行标红高亮，一眼定位
- 选中仓库弹出详情页：
  - 可点击的变更文件列表，点进任一文件查看逐行着色的 diff
  - 待 pull（incoming）/ 待 push（outgoing）的提交，含作者、时间与变更文件清单
  - stash 列表
- 正确处理含空格 / 中文等特殊字符的文件名，diff 不会错位或空白
- 同时存在暂存与未暂存改动的文件，分两段完整展示
- 后台线程执行，UI 不卡顿

## 安装

```bash
pip install .
```

开发模式（改代码立即生效）。注意：若系统 setuptools 较旧或处于受限网络，可加 `--no-build-isolation` 用本机 setuptools 构建：

```bash
pip install -e .
# 受限环境下：
pip install --no-build-isolation -e .
```

## 使用

在包含多个 Git 仓库的父目录下运行：

```bash
gitgrid
```

### 快捷键

| 键 | 作用 |
|----|------|
| `↑` / `↓` | 在仓库 / 文件列表间移动 |
| `Enter` | 选中仓库进入详情；在详情页选中文件查看 diff |
| `r` | 刷新状态（会执行 fetch） |
| `f` | 开关自动 fetch（关闭后只看本地缓存，速度快） |
| `Esc` | 从详情 / diff 视图返回上一层 |
| `q` | 退出（在详情 / diff 视图中为返回） |
