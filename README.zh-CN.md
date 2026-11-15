# repoArchiver

[![CI](https://github.com/Smart-Zihan/repoArchiver/actions/workflows/ci.yml/badge.svg)](https://github.com/Smart-Zihan/repoArchiver/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

> 使用中的疑问，请到
> [Discussions](https://github.com/Smart-Zihan/repoArchiver/discussions) 提问。
> Issue 用于 bug 报告和功能建议。

把一个 git 仓库存成一个目录：代码历史存成 git bundle，再加上 release 元数据和发行附件。
之后一条命令就能刷新，也能在没有原站点的机器上还原。

[English](README.md) · [更新日志](CHANGELOG.md) · [参与开发](CONTRIBUTING.md) · [路线图](docs/ROADMAP.md)

## 做什么

- 写 `code.bundle`，就是普通 git bundle。`git clone code.bundle` 不需要本工具也能用。
- 把 release 元数据存成 JSON，并下载附件，包括平台自动生成的源码包。
- 被追踪的归档可以手动 `sync`，也可以在读取前自动 `trace`。
- 能还原分支、tag 或提交，可选同时还原该版本的附件。
- 只依赖 Python 3.11+ 和 `git`，没有其他依赖。

支持的来源：GitHub（含 Enterprise）、GitLab、Gitea/Forgejo（含 Codeberg）、Bitbucket，以及本地路径。

## 安装

```bash
pipx install .        # 或 pip install .
repoArchiver --help
```

不安装也能直接跑：

```bash
python -m repoArchiver --help
```

## 快速上手

```bash
repoArchiver archive https://github.com/psf/requests              # 代码 + release 元数据
repoArchiver archive https://github.com/psf/requests --only-releases -t nightly

repoArchiver archives                 # 每个归档一行
repoArchiver list psf__requests       # 某个归档的 refs 与 releases
repoArchiver restore psf__requests v2.32.3 -d ./requests-2.32.3

repoArchiver sync                     # 刷新所有被追踪归档
```

## 存了什么

```
archives/<owner>__<repo>/
├─ code.bundle             # base bundle；--only-releases 时不存在
├─ code-<UTC>.bundle       # --incremental 产生的增量
├─ releases/
│  ├─ index.json           # release 元数据
│  └─ assets/<tag>/…       # 已下载的附件与源码包
├─ meta.json               # 来源、时间、范围、选项
├─ state.json              # 仅 -t/--tracker 时生成
└─ history.jsonl           # 每次有变化的 sync 追加一行
```

## 命令

| 命令 | 简写 | 用途 |
|---|---|---|
| `archive` | `-a` | 创建或刷新归档 |
| `sync` | `-s` | 刷新被追踪归档，`--dry-run` 只报告 |
| `archives` | `ls` | 列出磁盘上的归档 |
| `list` | `-l` | 查看单个归档的 refs 与 releases |
| `restore` | `-x` | 还原某个 ref、tag 或提交 |
| `inspect` | `-i` | 查看归档元数据 |
| `verify` | `check` | 一致性校验 |
| `compact` | | 把增量链折成单个 bundle |
| `schedule` | | 打印 cron / schtasks / systemd 条目 |

全局选项放在子命令之前：`--lang`、`--archives-dir`、`--progress`、`--quiet`、`--verbose`、
`--trace` / `--no-trace`、`--version`。每个子命令都有自己的 `--help`。

### 归档范围

| 范围 | 参数 | 存什么 | 是否用 git |
|---|---|---|---|
| 全部（默认） | | bundle + release 元数据（加 `--with-assets` 还有附件） | 是 |
| 只有 Releases | `--only-releases` | release 元数据与附件 | 否 |
| 只有代码 | `--only-code` | 只有 bundle | 是 |

`--only-releases` 不 clone，也不需要装 `git`。`--only-code` 不会访问 release API。

### 代码怎么存

| 策略 | 参数 | 结果 | 历史 | 提交哈希 |
|---|---|---|---|---|
| 完整历史（默认） | | 全部 | 完整 | 不变 |
| 快照 | `--snapshot` | 每个 ref 一个无父提交 | 无 | 全部变化 |
| 重写历史 | `--rewrite-history` | 保留截止点之后的提交，更早的父提交摘掉 | 截止点之后 | 全部变化 |

bundle 必须自包含，所以没有「保留 T 之后的历史且哈希不变」这个选项，只有上面三种。
`--rewrite-history` 会保留 tree、作者、提交者、提交信息和合并结构，新旧 tip 的对应关系记在
`meta.json` 的 `rewrite.refs`。

`--since DATE` 和 `--since-tag TAG` 设定包含边界的截止点。它们总是过滤 release；在代码侧要配合
`--snapshot` 或 `--rewrite-history` 才生效。

### refs 与孤儿提交

默认存所有分支和 tag，不存 `refs/remotes/*`，这样 `restore main` 不会有歧义。

| 参数 | 含义 |
|---|---|
| `--include-ref GLOB` | 只存匹配的 ref（可重复） |
| `--exclude-ref GLOB` | 跳过匹配的 ref，其历史不入库 |
| `--keep-orphans` | 把没有 ref 指向的提交 pin 到 `refs/repoArchiver/orphans/` |

规则是 git 风格 glob，匹配完整 ref 名。写 `main` 或 `v*` 这类裸名字会同时匹配
`refs/heads/…` 与 `refs/tags/…`。exclude 优先。这是真实裁剪：只被排除 ref 引用的历史不会写入。
release 选择器只影响下载，两者语义不同。

### 增量 bundle

`--incremental` 保留第一个 bundle 作为 base，之后追加 `code-<时间戳>.bundle`，里面只放相对于已归档
tip 新增的对象。`restore` 先重放 base 再重放增量，用法不变。如果某个 ref 被删除或 force-push 回退，
增量表达不了，会重建 base 并给出警告。`compact` 把整条链折回一个 `code.bundle`。

### Releases、附件与选择器

`releases/index.json` 把四种平台的格式统一成一套。每个附件带 `kind`：`asset` 是上传到 release 的，
`source` 是平台生成的 zip/tar.gz。

| 参数 | 含义 |
|---|---|
| `--release GLOB` / `--skip-release GLOB` | 按 tag 包含 / 排除 release |
| `--asset GLOB` / `--skip-asset GLOB` | 按文件名或 `tag/文件名` 包含 / 排除附件 |
| `--no-source` | 不保存自动生成的源码包 |

选择器只影响下载。被排除的版本和附件仍留在索引里（`local_path: null`），以后可以补。选择器会写入
元数据，`sync` 自动沿用。

### 平台识别

按主机名识别平台。自建实例的域名不含关键词时，用 `--platform gitea`（或 `gitlab`、`github`、
`bitbucket`）指定，或在配置里写 `platform`。识别不出来的主机照常归档代码，只跳过 release 元数据并
给出警告。

Bitbucket 没有 release 概念：每个 tag 变成一条 release，附件是自动生成的源码包；仓库的 Downloads
另存为一条没有日期的记录。

### 追踪

`archive -t NAME` 会写 `state.json`，这个归档就算被追踪：

```bash
repoArchiver archive https://github.com/psf/requests --only-code --incremental -t nightly
repoArchiver sync                 # 所有被追踪归档
repoArchiver sync -t nightly
repoArchiver sync --dry-run
```

`trace` 是同一套刷新的自动版：在 `list`、`inspect`、`restore` 之前执行，只针对远端源。
失败只打警告，不会阻断你本来要执行的命令。用 `--no-trace` 或 `trace = false` 关掉，
`trace_interval` 用来节流（例如 `"6h"`）。

`schedule` 只打印 cron、schtasks 和 systemd 条目，不会安装任何东西。

### 还原

1. `ref` 的解析顺序：完整 ref 名，唯一的 `/` 后缀（`heads/main`、`tags/v1.0.0`），唯一的叶子名。
   有歧义时列出候选。唯一的缩写提交哈希（7 位以上十六进制）也可以。
2. 分支还原成本地分支。tag 仍是 tag，annotated tag 保持原样。其他情况是 detached checkout。
3. ref 不在 bundle 里但存在同名 release 时逐级降级：先试提交，再试默认分支，最后只还原附件。
   每一步都会打警告。加 `--strict` 则直接失败。
4. 只有 Releases 的归档把附件还原到 `<目标>/assets/`。
5. 只还原归档里真实存在的附件，`--fetch-missing` 会现场下载其余的。
6. 目标目录非空时默认拒绝，除非加 `--force`。文件系统根目录、家目录和当前目录一律不接受。

### 校验

`verify` 核对元数据与磁盘是否一致：bundle 链能在临时目录重放、refs 计数吻合、release 索引可解析、
记录的附件存在且大小一致、`state.json` 不与 `meta.json` 矛盾、`history.jsonl` 没有坏行。
有 error 退出码为 1，warning 不影响，除非加 `--strict`。`--json` 给脚本用。

### 归档位置

```bash
repoArchiver archive ./checkout --dir /data/archives/requests     # 单个归档放到别处
repoArchiver --archives-dir /mnt/backup/archives archive <url>    # 本次运行换根目录
repoArchiver restore /data/archives/requests v2.32.3 -d out       # 用路径代替 id
```

所有接受归档 id 的命令也接受路径。不带参数的 `sync`、`archives`、`verify` 只扫描归档根目录，
放在别处的归档要显式传路径。

### 进度输出

耗时步骤会先报一声，git 的进度会转发，下载画进度条，超过五秒没有输出会打心跳。

```
→ 归档 https://github.com/psf/requests 的代码
→ 正在 clone 到工作镜像
  Receiving objects 42%
→ 写入 code.bundle
  Writing objects 100%
已归档 "psf__requests" -> archives/psf__requests
  refs：312  releases：48  bundle：84.3 MiB  附件：96
```

| 参数 | 行为 |
|---|---|
| `--progress auto`（默认） | 终端上显示进度条与心跳，输出被重定向时静默 |
| `--progress always` | stderr 不是终端时也报阶段与里程碑 |
| `--progress never`、`-q` | 只留最终结果 |
| `--verbose` | 额外转发 git 原始输出 |

进度只写 stderr，stdout 只有结果。

### 界面语言

内置英文和简体中文：

```bash
repoArchiver --lang zh archives
REPOARCHIVER_LANG=zh repoArchiver verify --all
```

优先级：`--lang`，然后配置里的 `language`，然后 `$REPOARCHIVER_LANG` 或系统 locale，最后英文。
文案在 `repoArchiver/i18n.py`，新增语言见 [CONTRIBUTING](CONTRIBUTING.md)。

## 配置

可选，默认读 `~/.repoArchiver/config.toml`，带注释的样例在
[`examples/config.toml`](examples/config.toml)。

```toml
archives_dir = "archives"   # 相对路径按当前工作目录解析
language = "en"             # en | zh
progress = "auto"           # auto | always | never
trace = true                # 读取类命令执行前刷新被追踪归档
trace_interval = "6h"       # 给启动时的 trace 节流；0 表示每次都跑

[tokens]                    # 按主机给 token，"*" 是兜底
"github.com" = "ghp_xxx"

retries = 3                 # 5xx / 408 / 425 / 429 与网络错误
timeout = 60                # 单次请求超时，秒
min_interval = 0            # 同一主机的最小请求间隔，秒
resume = true               # 断点续传
```

token 的环境变量兜底：`GITHUB_TOKEN`/`GH_TOKEN`、`GITLAB_TOKEN`、`GITEA_TOKEN`/`CODEBERG_TOKEN`、
`BITBUCKET_TOKEN`/`BITBUCKET_APP_PASSWORD`。`-c/--config PATH` 指定其它文件（必须存在）；
默认配置不存在时按默认值运行。

## 退出码

| 码 | 含义 |
|---|---|
| `0` | 成功；降级、警告、附件失败都算成功 |
| `1` | 可预期错误：配置、归档、git、ref、网络；`sync` 失败和 `verify` 发现 error 也是 1 |
| `2` | 命令行用法错误 |

## 尚未实现

- 增量链的自动压实（现在要手动 `compact`）
- 浅历史归档（`--depth`）：bundle 不支持 shallow，需要另一套布局
- 附件内容哈希（`verify` 只比大小）
- 并发下载附件
- 归档目录移动后重写 `local_path`

设计取舍见 [docs/ROADMAP.md](docs/ROADMAP.md)。

## 开发

```bash
python -m unittest discover -s tests -v
python -m ruff check repoArchiver tests
```

测试只用标准库，不访问网络。测试会自己建临时 git 仓库、构造归档目录，并打桩平台 API、HTTP 层和下载
步骤。临时目录建在仓库内的 `.test-tmp/`（可用 `REPOARCHIVER_TEST_TMP` 覆盖），所以在系统临时目录
受限的环境里也能跑。

模块划分：

```
repoArchiver/
├─ cli.py          argparse、子命令分发、全局选项
├─ archiver.py     归档管道（archive / sync 共用）
├─ platforms/      github / gitlab / gitea / bitbucket 适配
├─ http.py         重试、限速、断点续传
├─ releases.py     release 索引与附件下载
├─ bundle.py       git 子进程与 bundle 封装
├─ chain.py        增量 bundle 链
├─ rewrite.py      按截止点重写历史
├─ cutoff.py       截止点解析、ref 选择、快照
├─ refs.py         ref 包含/排除、孤儿 pin
├─ tags.py         annotated tag 读取与重建
├─ restore.py      还原逻辑与 ref 解析
├─ track.py        sync / trace
├─ verify.py       一致性校验
├─ schedule.py     调度条目与 trace 节流
├─ selection.py    release / 附件选择器
├─ layout.py       配置、路径、JSON 元数据
├─ progress.py     阶段提示、git 进度、下载进度条
├─ i18n.py         文案目录（en / zh）
└─ errors.py       异常与警告输出
```

## 许可

MIT，见 [LICENSE](LICENSE)。
