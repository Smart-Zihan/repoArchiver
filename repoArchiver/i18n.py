"""Interface localization."""

from __future__ import annotations

import os
from typing import Any

DEFAULT_LANGUAGE = "en"
LANGUAGES = ("en", "zh")

_ALIASES = {
    "en": "en",
    "en_us": "en",
    "en-us": "en",
    "en_gb": "en",
    "en-gb": "en",
    "english": "en",
    "zh": "zh",
    "zh_cn": "zh",
    "zh-cn": "zh",
    "zh_hans": "zh",
    "zh-hans": "zh",
    "zh_sg": "zh",
    "zh-sg": "zh",
    "zh_tw": "zh",
    "zh-tw": "zh",
    "chinese": "zh",
    "cn": "zh",
}

LANGUAGE_LABELS = {"en": "English", "zh": "简体中文"}

_LANGUAGE = DEFAULT_LANGUAGE


def normalize_language(value: object) -> str | None:
    """Map a user-supplied language tag to a supported one, or None."""

    if not isinstance(value, str):
        return None
    text = value.strip().lower().replace(" ", "")
    if not text:
        return None
    if text in _ALIASES:
        return _ALIASES[text]
    base = text.replace("_", "-").split("-", 1)[0]
    return _ALIASES.get(base)


def language_from_env() -> str | None:
    """Language hinted by the environment (REPOARCHIVER_LANG first)."""

    for name in ("REPOARCHIVER_LANG", "LC_ALL", "LC_MESSAGES", "LANG"):
        candidate = normalize_language(os.environ.get(name))
        if candidate is not None:
            return candidate
    return None


def set_language(value: object) -> str:
    """Set the active language; unsupported values fall back to English."""

    global _LANGUAGE
    _LANGUAGE = normalize_language(value) or DEFAULT_LANGUAGE
    return _LANGUAGE


def get_language() -> str:
    """Return the active language tag."""

    return _LANGUAGE


def resolve_language(*, flag: object = None, config: object = None, env: bool = True) -> str:
    """Pick the language for this run, honouring flag > config > env."""

    for candidate in (flag, config):
        resolved = normalize_language(candidate)
        if resolved is not None:
            return resolved
    if env:
        hinted = language_from_env()
        if hinted is not None:
            return hinted
    return DEFAULT_LANGUAGE


def _format(template: str, fields: dict[str, Any]) -> str:
    if not fields:
        return template
    try:
        return template.format(**fields)
    except (KeyError, IndexError, ValueError):
        return template


def tr(message: str, /, **fields: Any) -> str:
    """Translate message (English text used as the id) and fill it in."""

    template = _CATALOG.get(_LANGUAGE, {}).get(message)
    return _format(template if template is not None else message, fields)


def _catalog_zh() -> dict[str, str]:
    """Simplified Chinese catalog."""

    return {
        "warning: {message}": "警告：{message}",
        "interrupted": "已中断",
        'ref "{ref}" not found': '未找到 ref "{ref}"',
        'ref "{ref}" not found in archive "{archive_id}"': '归档 "{archive_id}" 中未找到 ref "{ref}"',
        'ambiguous ref "{ref}", candidates: {candidates} (use the full name and retry)': (
            'ref "{ref}" 有歧义，候选：{candidates}（请改用完整名称重试）'
        ),
        'archive "{archive_id}" not found under "{archives_dir}" '
        "(run `repoArchiver archive ...` first)": (
            '在 "{archives_dir}" 下未找到归档 "{archive_id}"（请先运行 `repoArchiver archive ...`）'
        ),
        'archive not found: "{path}"': '未找到归档："{path}"',
        'archive path "{path}" exists and is not a directory': '归档路径 "{path}" 已存在但不是目录',
        'archive metadata not found: "{path}"': '未找到归档元数据："{path}"',
        'updating existing archive "{archive_id}"': '正在更新已有归档 "{archive_id}"',
        'local repository not found: "{path}"': '本地仓库不存在："{path}"',
        'repository "{source}" has no refs; nothing to archive': '仓库 "{source}" 没有任何 ref，无可归档内容',
        "repository has no branches or tags to snapshot": "仓库没有可快照的分支或 tag",
        "no branches or tags at/after the cutoff ({cutoff})": "截止点（{cutoff}）及之后没有分支或 tag",
        "no refs left after applying {rules}; nothing to archive": "应用 {rules} 后没有剩余 ref，无可归档内容",
        'archive "{archive_id}" is not tracked '
        "(run `repoArchiver archive -t NAME <url>` first)": (
            '归档 "{archive_id}" 未被追踪（请先运行 `repoArchiver archive -t NAME <url>`）'
        ),
        "--snapshot stores code, so it cannot be combined with --only-releases": (
            "--snapshot 会存代码，不能与 --only-releases 同时使用"
        ),
        "--rewrite-history/--incremental store code, so they cannot be combined "
        "with --only-releases": (
            "--rewrite-history/--incremental 会存代码，不能与 --only-releases 同时使用"
        ),
        "--with-assets stores releases, so it cannot be combined with --only-code": (
            "--with-assets 会存 releases，不能与 --only-code 同时使用"
        ),
        "use either --snapshot or --rewrite-history, not both": "--snapshot 与 --rewrite-history 只能选一个",
        "--rewrite-history rewrites every commit, so --incremental cannot help": (
            "--rewrite-history 会重写所有提交，--incremental 帮不上忙"
        ),
        "--rewrite-history needs a cutoff: pass --since or --since-tag": (
            "--rewrite-history 需要截止点：请给 --since 或 --since-tag"
        ),
        "refusing to use a filesystem root as the restore destination": "拒绝把文件系统根目录作为还原目标",
        "refusing to use the home directory as the restore destination": "拒绝把家目录作为还原目标",
        "refusing to restore into the current working directory; pass --dest": (
            "拒绝还原到当前工作目录；请用 --dest 指定"
        ),
        'destination "{target}" exists and is not a directory': '目标 "{target}" 已存在但不是目录',
        'destination "{target}" is not empty (use --force to overwrite)': '目标 "{target}" 非空（用 --force 覆盖）',
        'tag "{tag}" is not in the bundle; --strict forbids falling back': (
            'tag "{tag}" 不在 bundle 里；--strict 禁止降级'
        ),
        "tag {tag} not in bundle, falling back to commit {commit}": "tag {tag} 不在 bundle 里，改用提交 {commit}",
        "tag {tag} not in bundle and the release records no commit hash, "
        "falling back to the default branch": "tag {tag} 不在 bundle 里，且 release 未记录提交号，改用默认分支",
        "commit {commit} is not in the bundle, falling back to the default branch": (
            "提交 {commit} 不在 bundle 里，改用默认分支"
        ),
        "the bundle has no usable default branch": "bundle 里没有可用的默认分支",
        'restoring the default branch "{branch}" instead': '改为还原默认分支 "{branch}"',
        "failed to restore code: {detail}": "还原代码失败：{detail}",
        'neither code nor assets could be restored for tag "{tag}"': 'tag "{tag}" 的代码与附件都无法还原',
        "assets only: no code was restored": "仅还原了附件，没有还原代码",
        'archive "{archive_id}" stores no code, and "{ref}" matches no release': (
            '归档 "{archive_id}" 不含代码，且 "{ref}" 不匹配任何 release'
        ),
        'archive "{archive_id}" stores no code; restoring the assets of {tag} only': (
            '归档 "{archive_id}" 不含代码，只还原 {tag} 的附件'
        ),
        'no assets could be restored for tag "{tag}"': 'tag "{tag}" 的附件都无法还原',
        'no release matches "{ref}"; assets skipped': '没有匹配 "{ref}" 的 release，跳过附件',
        "skipped {count} asset(s) not stored in this archive{hint}": "跳过 {count} 个未存入本归档的附件{hint}",
        " (use --fetch-missing to download them)": "（可用 --fetch-missing 现场下载）",
        "failed to download asset {name}: invalid asset name": "下载附件 {name} 失败：附件名无效",
        "failed to download asset {name}: no local copy and no download URL": (
            "下载附件 {name} 失败：既无本地副本也无下载地址"
        ),
        "failed to download asset {name}: no download URL in release metadata": (
            "下载附件 {name} 失败：release 元数据里没有下载地址"
        ),
        "failed to download asset {name}: {detail}": "下载附件 {name} 失败：{detail}",
        'bundle not found: "{path}"': '未找到 bundle："{path}"',
        "bundle file(s) missing: {names}": "缺少 bundle 文件：{names}",
        'cannot remove "{path}": {detail}': '无法删除 "{path}"：{detail}',
        "the source no longer contains every commit of the existing bundle "
        "(force-push or deleted branch); rebuilding the full bundle": (
            "源仓库已不包含现有 bundle 的全部提交（force-push 或分支被删），将重建完整 bundle"
        ),
        "refs changed in a way an increment cannot express; rebuilding the full bundle": (
            "refs 的变动无法用增量表达（删除或回退），改为重建完整 bundle"
        ),
        'no release with tag "{tag}", no assets to download': '没有 tag 为 "{tag}" 的 release，无需下载附件',
        "skipped {count} release(s) without a parseable publication date ({cutoff})": (
            "跳过 {count} 个没有可解析发布日期的 release（{cutoff}）"
        ),
        "skipped {count} ref(s) that do not point to a commit": "跳过 {count} 个不指向提交的 ref",
        "cutoff trims code only with --snapshot or --rewrite-history; "
        "this code-only archive keeps the full history": (
            "截止点只有在 --snapshot 或 --rewrite-history 下才会裁剪代码；本次仅代码归档保留完整历史"
        ),
        "cutoff {cutoff}: kept {kept} of {total} release(s)": "截止点 {cutoff}：保留 {kept}/{total} 个 release",
        "cutoff filters release metadata only; repository history is kept "
        "in full (add --snapshot or --rewrite-history to trim the code)": (
            "截止点只过滤 release 元数据，仓库历史完整保留（要裁剪代码请加 --snapshot 或 --rewrite-history）"
        ),
        "code-only scope: existing release metadata is kept but not refreshed "
        '(delete "releases/" to drop it)': '仅代码范围：已有 release 元数据保留但不刷新（删掉 "releases/" 即可丢弃）',
        'releases-only scope: "{bundle}" is left on disk but no longer '
        "referenced by meta.json": '仅 Releases 范围："{bundle}" 仍留在磁盘上，但 meta.json 已不再引用',
        "local repository source, release metadata skipped": "本地仓库源，跳过 release 元数据",
        "no {platform} repository {slug} (HTTP 404); skipping release metadata": (
            "没有 {platform} 仓库 {slug}（HTTP 404），跳过 release 元数据"
        ),
        "{platform} API rejected the request ({detail}); skipping release metadata "
        "(set a token for private repositories)": (
            "{platform} API 拒绝了请求（{detail}），跳过 release 元数据（私有仓库请配置 token）"
        ),
        "{platform} API request failed ({detail}); skipping release metadata": (
            "{platform} API 请求失败（{detail}），跳过 release 元数据"
        ),
        "releases API is not implemented for host {host}; skipping release metadata "
        "(pass --platform to pick one)": "主机 {host} 的 release API 尚未支持，跳过 release 元数据（可用 --platform 指定）",
        "→ {message}": "→ {message}",
        "… still working ({seconds}s)": "… 仍在处理（{seconds}s）",
        "writing {name}": "写入 {name}",
        "cloning {source} into a work mirror": "正在 clone {source} 到工作镜像",
        "copying the git directory of {source}": "复制 {source} 的 git 目录",
        "replaying {count} bundle(s)": "重放 {count} 个 bundle",
        "archiving code from {source}": "归档 {source} 的代码",
        "fetching release metadata from {source}": "从 {source} 抓取 release 元数据",
        "downloading assets of {count} release(s)": "下载 {count} 个 release 的附件",
        "downloaded {name} ({size} bytes)": "已下载 {name}（{size} 字节）",
        "downloaded asset {tag}/{name} ({size} bytes)": "已下载附件 {tag}/{name}（{size} 字节）",
        'archived "{archive_id}" -> "{out_dir}"': '已归档 "{archive_id}" -> "{out_dir}"',
        "releases only": "仅 Releases",
        "code only": "仅代码",
        "snapshot": "快照",
        "incremental": "增量",
        "rewrite: {detail}": "历史重写：{detail}",
        "orphans: {count}": "孤儿提交：{count}",
        "cutoff {label}": "截止点 {label}",
        "refs: {count}": "refs：{count}",
        "releases: {count}": "releases：{count}",
        "bundle: {size}": "bundle：{size}",
        "assets: {count}": "附件：{count}",
        "selection: {detail}": "选择器：{detail}",
        "refs rules: {detail}": "refs 规则：{detail}",
        'no tracked archives found (run `repoArchiver archive -t NAME <url>` first)': (
            "没有找到被追踪的归档（请先运行 `repoArchiver archive -t NAME <url>`）"
        ),
        'synced "{archive_id}": {detail}': '已同步 "{archive_id}"：{detail}',
        'would sync "{archive_id}": {detail}': '将会同步 "{archive_id}"：{detail}',
        'sync of "{archive_id}" failed: {detail}': '"{archive_id}" 同步失败：{detail}',
        'skipping "{archive_id}": {detail}': '跳过 "{archive_id}"：{detail}',
        'trace of "{archive_id}" failed: {detail}': '跟进 "{archive_id}" 失败：{detail}',
        "trace {archive_id}: {detail}": "跟进 {archive_id}：{detail}",
        "up to date": "已是最新",
        "+{count} release(s)": "+{count} 个 release",
        "+{count} asset(s)": "+{count} 个附件",
        "+{count} ref/commit(s)": "+{count} 个 ref/提交",
        "{count} failed asset(s)": "{count} 个附件失败",
        "archive: {archive_id}  source: {source}": "归档：{archive_id}  源：{source}",
        "refs: {refs}  releases: {releases}": "refs：{refs}  releases：{releases}",
        "code: none (releases-only archive)": "代码：无（仅 Releases 归档）",
        "code: {count} bundles (incremental chain)": "代码：{count} 个 bundle（增量链）",
        'missing bundle file(s): {names}': "缺少 bundle 文件：{names}",
        "(no matching refs)": "（没有匹配的 ref）",
        "ID": "ID",
        "SCOPE": "范围",
        "REFS": "REFS",
        "RELEASES": "RELEASES",
        "SIZE": "体积",
        "TRACKER": "追踪",
        "LAST SYNC": "最后同步",
        "{count} archive(s) under {path}": "{path} 下共 {count} 个归档",
        'no archives found under "{archives_dir}"': '"{archives_dir}" 下没有归档',
        'restored "{ref}" from archive "{archive_id}" into "{dest}"': (
            '已从归档 "{archive_id}" 还原 "{ref}" 到 "{dest}"'
        ),
        "archive: {archive_id}": "归档：{archive_id}",
        "path: {path}": "路径：{path}",
        "source: {source}": "源：{source}",
        "archived_at: {when}": "归档时间：{when}",
        "scope: {scope}": "范围：{scope}",
        "platform: {name}": "平台：{name}",
        "assets: {saved}/{total} saved ({source} source)": "附件：{saved}/{total} 已保存（其中 {source} 个源码包）",
        "incremental: yes": "增量：是",
        "source mode: {mode}": "代码模式：{mode}",
        "rewrite: {commits} commit(s) kept": "历史重写：保留 {commits} 个提交",
        "cutoff: {label} ({instant})": "截止点：{label}（{instant}）",
        "tracker state:": "追踪状态：",
        "tracker state: none": "追踪状态：无",
        "bundle: none (releases-only archive)": "bundle：无（仅 Releases 归档）",
        "bundle: {name} ({size})": "bundle：{name}（{size}）",
        "bundle: {name} (missing)": "bundle：{name}（缺失）",
        "chain: {count} bundle(s), incremental": "链：{count} 个 bundle，增量模式",
        "releases:": "releases：",
        "history:": "变更历史：",
        "prerelease": "预发布",
        "draft": "草稿",
        "no changes": "无变化",
        "{count} failed": "{count} 个失败",
        "+{count} {kind}(s)": "+{count} {kind}",
        "release": "个 release",
        "asset": "个附件",
        "ref": "个 ref",
        "{archive_id}: {status}  ({path})": "{archive_id}：{status}  （{path}）",
        "ok": "正常",
        "FAILED": "失败",
        "(warnings do not fail verify; use --strict to change that)": (
            "（警告不会让 verify 失败，用 --strict 改变这一行为）"
        ),
        "{count} archive checked, {errors} error(s), {warnings} warning(s)": (
            "{count} 个归档已检查，{errors} 个错误，{warnings} 个警告"
        ),
        "{count} archives checked, {errors} error(s), {warnings} warning(s)": (
            "{count} 个归档已检查，{errors} 个错误，{warnings} 个警告"
        ),
        '"{archive_id}" is already a single bundle; nothing to compact': '"{archive_id}" 已经是单个 bundle，无需压实',
        'compacted "{archive_id}": {count} bundle(s) -> code.bundle ({size})': (
            '已压实 "{archive_id}"：{count} 个 bundle -> code.bundle（{size}）'
        ),
        "cron (Linux / macOS):": "cron（Linux / macOS）：",
        "Windows (schtasks, run once in cmd.exe):": "Windows（在 cmd.exe 中运行一次 schtasks）：",
        "systemd timer (OnUnitActiveSec line of repoarchiver.timer):": (
            "systemd timer（repoarchiver.timer 的 OnUnitActiveSec 行）："
        ),
        "# Nothing is installed automatically; copy the line you need.": "# 不会自动安装任何东西，复制你需要的行即可。",
        "tip: set trace_interval in the config to throttle the on-launch trace "
        '(e.g. trace_interval = "6h") instead of relying on a scheduler.': (
            '提示：在配置里设置 trace_interval（例如 trace_interval = "6h"）即可给启动时的 trace 节流，'
            "不必依赖调度器。"
        ),
        "archive a repository": "归档一个仓库",
        "refresh tracked archives": "刷新被追踪的归档",
        "list refs and releases of an archive": "列出归档的 refs 与 releases",
        "list the archives on disk": "列出磁盘上的归档",
        "restore a ref from an archive": "从归档还原一个 ref",
        "show archive metadata": "显示归档元数据",
        "check archives for inconsistencies": "检查归档的一致性",
        "fold an incremental bundle chain into one bundle": "把增量 bundle 链折成单个 bundle",
        "print scheduler entries for periodic syncs": "打印定期同步的调度条目",
        "config file (default: ~/.repoArchiver/config.toml)": "配置文件（默认 ~/.repoArchiver/config.toml）",
        "repository URL or local path": "仓库 URL 或本地路径",
        "archive id (e.g. psf__requests) or archive path": "归档 id（如 psf__requests）或归档路径",
        "branch, tag, or full ref name": "分支、tag 或完整 ref 名",
        "destination directory (default: ./<repo>)": "目标目录（默认 ./<repo>）",
        "machine-readable output": "机器可读输出",
        "only download releases whose tag matches GLOB (repeatable)": "只下载 tag 匹配 GLOB 的 release（可重复）",
        "skip releases whose tag matches GLOB (repeatable)": "跳过 tag 匹配 GLOB 的 release（可重复）",
        "only download assets matching GLOB (repeatable)": "只下载匹配 GLOB 的附件（可重复）",
        "skip assets matching GLOB (repeatable)": "跳过匹配 GLOB 的附件（可重复）",
        "do not save the auto-generated source zip/tar.gz": "不保存自动生成的源码 zip/tar.gz",
        "only archive refs matching GLOB, e.g. refs/heads/main (repeatable)": (
            "只归档匹配 GLOB 的 ref，例如 refs/heads/main（可重复）"
        ),
        "skip refs matching GLOB; their history is not stored (repeatable)": (
            "跳过匹配 GLOB 的 ref，其历史不会存入（可重复）"
        ),
        "pin commits no ref points at (dangling work) under refs/repoArchiver/orphans/": (
            "把没有 ref 指向的提交（游离提交）pin 到 refs/repoArchiver/orphans/"
        ),
        "interface language: {languages} (default: config, $REPOARCHIVER_LANG, or the locale)": (
            "界面语言：{languages}（默认取配置、$REPOARCHIVER_LANG 或系统 locale）"
        ),
        "directory holding the archives (default: config key archives_dir, i.e. ./archives)": (
            "存放归档的目录（默认取配置项 archives_dir，即 ./archives）"
        ),
        "progress output: auto (only on a terminal), always, never (default: {default})": (
            "进度输出：auto（仅终端显示）、always、never（默认：{default}）"
        ),
        "same as --progress never": "等同于 --progress never",
        "also forward the raw git output while working": "同时转发 git 的原始输出",
        "refresh tracked archives before running the command (default: config 'trace')": (
            "执行命令前先刷新被追踪的归档（默认取配置 trace）"
        ),
        "never refresh tracked archives before running the command": "执行命令前不刷新被追踪的归档",
        "tracker label; writing state.json marks the archive as tracked "
        "(then `sync`/`trace` keep it up to date)": (
            "追踪标签；写入 state.json 后该归档即被追踪（之后由 `sync`/`trace` 跟进）"
        ),
        "store the archive in DIR instead of <archives-dir>/<owner>__<repo> "
        "(later commands accept that path as the archive id)": (
            "把归档存到 DIR，而不是 <archives-dir>/<owner>__<repo>（后续命令可直接用该路径当 archive id）"
        ),
        "store release metadata and assets only (no code.bundle, no clone)": (
            "只存 release 元数据与附件（不生成 code.bundle，也不 clone）"
        ),
        "store code.bundle only (the release API is never queried)": "只存 code.bundle（完全不访问 release API）",
        "download release assets (default with --only-releases)": "下载 release 附件（--only-releases 下默认开启）",
        "record release metadata without downloading assets": "只记录 release 元数据，不下载附件",
        "only keep releases and snapshot refs at/after DATE (ISO 8601, e.g. 2024-01-01)": (
            "只保留 DATE（ISO 8601，如 2024-01-01）及之后的 release 与快照 ref"
        ),
        "like --since, using TAG's release date (or commit date) as the cutoff": (
            "同 --since，但用 TAG 的 release 日期（或提交时间）作为截止点"
        ),
        "store code-only snapshots (one root commit per ref, no history)": "只存代码快照（每个 ref 一个无父提交，不含历史）",
        "keep commits at/after the cutoff, rewritten so the bundle stands alone "
        "(hashes change; needs --since or --since-tag)": (
            "保留截止点及之后的提交，并重写成自包含 bundle（hash 会变；需配 --since 或 --since-tag）"
        ),
        "keep the first bundle as a base and append small deltas on refresh": (
            "第一个 bundle 作为 base，之后每次刷新只追加小增量"
        ),
        "forge behind a self-hosted host (default: detected from the host name)": "自建实例背后的平台（默认按主机名识别）",
        "archives to sync (default: every tracked archive); an archive path works too": (
            "要同步的归档（默认全部被追踪归档）；也可以直接给归档路径"
        ),
        "only sync archives carrying this tracker label": "只同步带该 tracker 标签的归档",
        "report what would be added without downloading or writing": "只报告将会新增什么，不下载也不写盘",
        "only show refs starting with PREFIX (exact prefix, no fuzzy matching)": (
            "只显示以 PREFIX 开头的 ref（前缀精确匹配，不做模糊匹配）"
        ),
        "also show repoArchiver's internal refs (orphan pins)": "同时显示 repoArchiver 的内部 ref（孤儿 pin）",
        "overwrite a non-empty destination": "覆盖非空目标目录",
        "fail instead of falling back when a tag is missing from the bundle": "tag 不在 bundle 里时报错，而不是降级",
        "copy/download release assets": "复制/下载 release 附件",
        "also download assets this archive never stored (default: only restore archived ones)": (
            "连归档里从未下载的附件也现下（默认只还原已存的）"
        ),
        "restore the release assets of REF only, never the code": "只还原 REF 的发行附件，不要代码",
        "archives to check (default: all of them); an archive path works too": (
            "要检查的归档（默认全部）；也可以直接给归档路径"
        ),
        "check every archive under the archives directory (the default)": "检查归档目录下的全部归档（默认行为）",
        "treat warnings as errors too": "警告也视为失败",
        "how often to sync, e.g. 30m, 6h, 1d (default: 6h)": "同步间隔，如 30m、6h、1d（默认 6h）",
        "sync only these archives (default: every tracked archive)": "只同步这些归档（默认全部被追踪归档）",
        "Archive a repository's code bundle, its releases (metadata + assets, "
        "including the generated source archives), or both; keep tracked archives "
        "up to date and restore refs or release assets from them.": (
            "把仓库的代码 bundle、Releases（元数据 + 附件，含平台自动生成的源码包）或两者一起归档；"
            "持续跟进被追踪的归档，并能从归档中还原 ref 或发行物。"
        ),
        "Store code.bundle and/or release metadata (plus assets). --only-releases skips "
        "git entirely, --only-code skips the release API. --since/--since-tag cut off old "
        "releases; on the code side they need --snapshot or --rewrite-history. "
        "--incremental appends deltas instead of rebuilding, and --include-ref/"
        "--exclude-ref prune refs for real.": (
            "存 code.bundle 与/或 release 元数据（可带附件）。--only-releases 完全不碰 git，"
            "--only-code 不查 release API。--since/--since-tag 用于裁掉旧 release；要在代码侧生效"
            "需配 --snapshot 或 --rewrite-history。--incremental 只追加增量而不重建，"
            "--include-ref/--exclude-ref 是真正的 ref 裁剪。"
        ),
        "Re-fetch releases and assets (and code) for tracked archives. Only additions "
        "and missing files are downloaded; the result is logged to history.jsonl.": (
            "重新抓取被追踪归档的 releases 与附件（以及代码）。只下载新增和缺失的文件，结果记入 history.jsonl。"
        ),
        "Merge bundle refs and releases into one REF | HASH | TAG? | RELEASE? table.": (
            "把 bundle 里的 refs 与 releases 合成 REF | HASH | TAG? | RELEASE? 一张表。"
        ),
        "One row per archive under the archives directory: scope, refs, releases, size, "
        "tracker and last sync. Use `list` for the contents of one archive.": (
            "每个归档一行：范围、refs 数、release 数、体积、tracker、最后同步时间。查看单个归档的内容请用 `list`。"
        ),
        "Check out a ref from code.bundle; release assets are copied with --with-assets. "
        "Releases-only archives restore their assets instead of code.": (
            "从 code.bundle 检出某个 ref；加 --with-assets 时同时复制发行附件。仅 Releases 的归档会改为还原附件。"
        ),
        "Print a summary of meta.json, state.json, releases/index.json and history.": (
            "打印 meta.json、state.json、releases/index.json 与变更历史的摘要。"
        ),
        "Compare meta.json, the bundle chain, release metadata and the files on disk; "
        "exits 1 when an error is found.": (
            "核对 meta.json、bundle 链、release 元数据与磁盘文件；发现 error 时退出码为 1。"
        ),
        "Replay the chain and write a single self-contained code.bundle, then remove the "
        "delta files.": "重放整条链，写出单个自包含 code.bundle，然后删除增量文件。",
        "Print cron / schtasks / systemd snippets that run `sync` on an interval. "
        "Nothing is installed for you.": "打印按间隔运行 `sync` 的 cron / schtasks / systemd 片段。不会替你安装任何东西。",
    }


_CATALOG: dict[str, dict[str, str]] = {"en": {}, "zh": _catalog_zh()}


__all__ = [
    "DEFAULT_LANGUAGE",
    "LANGUAGES",
    "LANGUAGE_LABELS",
    "get_language",
    "language_from_env",
    "normalize_language",
    "resolve_language",
    "set_language",
    "tr",
]
