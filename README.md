# repoArchiver

[![CI](https://github.com/Smart-Zihan/repoArchiver/actions/workflows/ci.yml/badge.svg)](https://github.com/Smart-Zihan/repoArchiver/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](pyproject.toml)

> Usage questions? Ask in
> [Discussions](https://github.com/Smart-Zihan/repoArchiver/discussions).
> Issues are for bug reports and feature requests.

Store a git repository as a directory: the code history as a git bundle, plus release metadata
and release assets. Refresh it later with one command, and restore from it without the original
host.

[简体中文](README.zh-CN.md) · [Changelog](CHANGELOG.md) · [Contributing](CONTRIBUTING.md) · [Roadmap](docs/ROADMAP.md)

## What it does

- Writes `code.bundle`, an ordinary git bundle. `git clone code.bundle` works without this tool.
- Stores release metadata as JSON and downloads assets, including the source archives a forge
  generates automatically.
- Refreshes a tracked archive on demand (`sync`) or before you read it (`trace`).
- Restores a branch, tag or commit, and optionally that release's assets.
- Requires Python 3.11+ and the `git` binary. No other dependencies.

Sources: GitHub (including Enterprise), GitLab, Gitea/Forgejo (including Codeberg) and Bitbucket.
Local paths work too.

## Install

```bash
pipx install .        # or: pip install .
repoArchiver --help
```

Run without installing:

```bash
python -m repoArchiver --help
```

## Quick start

```bash
repoArchiver archive https://github.com/psf/requests              # code + release metadata
repoArchiver archive https://github.com/psf/requests --only-releases -t nightly

repoArchiver archives                 # one row per archive
repoArchiver list psf__requests       # refs and releases of one archive
repoArchiver restore psf__requests v2.32.3 -d ./requests-2.32.3

repoArchiver sync                     # refresh tracked archives
```

## What gets stored

```
archives/<owner>__<repo>/
├─ code.bundle             # base bundle; absent for --only-releases
├─ code-<UTC>.bundle       # deltas written by --incremental
├─ releases/
│  ├─ index.json           # release metadata
│  └─ assets/<tag>/…       # downloaded assets and source archives
├─ meta.json               # source, timestamps, scope, options
├─ state.json              # only with -t/--tracker
└─ history.jsonl           # one line per sync that changed something
```

## Commands

| Command | Short | Purpose |
|---|---|---|
| `archive` | `-a` | Create or refresh an archive |
| `sync` | `-s` | Refresh tracked archives; `--dry-run` reports only |
| `archives` | `ls` | List the archives on disk |
| `list` | `-l` | Show refs and releases of one archive |
| `restore` | `-x` | Check out a ref, tag or commit |
| `inspect` | `-i` | Metadata summary of one archive |
| `verify` | `check` | Consistency check |
| `compact` | | Merge an incremental chain into one bundle |
| `schedule` | | Print cron / schtasks / systemd entries |

Global options go before the subcommand: `--lang`, `--archives-dir`, `--progress`, `--quiet`,
`--verbose`, `--trace` / `--no-trace`, `--version`. Each subcommand has its own `--help`.

### Scope

| Scope | Flag | Stores | Uses git |
|---|---|---|---|
| all (default) | | bundle + release metadata (+ assets with `--with-assets`) | yes |
| releases | `--only-releases` | release metadata and assets | no |
| code | `--only-code` | bundle only | yes |

`--only-releases` does not clone and does not need `git` installed. `--only-code` never calls the
release API.

### How the code is stored

| Strategy | Flag | Result | History | Commit hashes |
|---|---|---|---|---|
| Full history (default) | | everything | complete | unchanged |
| Snapshot | `--snapshot` | one root commit per ref | none | all change |
| Rewritten history | `--rewrite-history` | commits at/after a cutoff, earlier parents dropped | since cutoff | all change |

A bundle has to be self-contained, so a shallow "history since T" is not offered; these three
strategies are. `--rewrite-history` keeps trees, authors, committers, messages and merge structure,
and records the old→new tips in `meta.json` under `rewrite.refs`.

`--since DATE` and `--since-tag TAG` set an inclusive cutoff. They always filter releases; on the
code side they only apply together with `--snapshot` or `--rewrite-history`.

### Refs and orphan commits

Every branch and tag is stored by default. `refs/remotes/*` is never stored, so `restore main` is
unambiguous.

| Flag | Meaning |
|---|---|
| `--include-ref GLOB` | store only matching refs (repeatable) |
| `--exclude-ref GLOB` | skip matching refs; their history is not stored |
| `--keep-orphans` | pin commits no ref points at under `refs/repoArchiver/orphans/` |

Patterns are git-style globs matched against the full ref name. A bare `main` or `v*` matches both
`refs/heads/…` and `refs/tags/…`. Excludes win. Unlike the release selectors, this is real pruning:
history reachable only from an excluded ref is not written.

### Incremental bundles

`--incremental` keeps the first bundle as a base and appends `code-<timestamp>.bundle` files holding
only objects that are new relative to the archived tips. `restore` replays base then deltas, so
commands do not change. If a ref is deleted or force-pushed backwards the delta cannot express it,
and the base is rebuilt with a warning. `compact` folds the chain back into one `code.bundle`.

### Releases, assets and selectors

`releases/index.json` normalizes all four platforms into one shape. Each asset has
`kind: "asset"` (uploaded to the release) or `kind: "source"` (generated zip/tar.gz).

| Flag | Meaning |
|---|---|
| `--release GLOB` / `--skip-release GLOB` | include / exclude releases by tag |
| `--asset GLOB` / `--skip-asset GLOB` | include / exclude assets by name or `tag/name` |
| `--no-source` | do not store generated source archives |

Selectors affect downloads only. Excluded releases and assets stay in the index with
`local_path: null`, so they can be fetched later. Selectors are saved, and `sync` reuses them.

### Platforms

The forge is detected from the host name. For a self-hosted instance with an unusual name, pass
`--platform gitea` (or `gitlab`, `github`, `bitbucket`) or set `platform` in the config. An unknown
host still archives code; release metadata is skipped with a warning.

Bitbucket has no release concept. Each tag becomes a release whose assets are the generated source
archives, and the repository Downloads area becomes one additional entry without a date.

### Tracking

`archive -t NAME` writes `state.json`, which marks the archive as tracked:

```bash
repoArchiver archive https://github.com/psf/requests --only-code --incremental -t nightly
repoArchiver sync                 # every tracked archive
repoArchiver sync -t nightly
repoArchiver sync --dry-run
```

`trace` runs the same refresh automatically before `list`, `inspect` and `restore`, for remote
sources only. A failed trace prints a warning and never blocks the command you asked for. Turn it
off with `--no-trace` or `trace = false`; `trace_interval` throttles it (for example `"6h"`).

`schedule` prints cron, schtasks and systemd lines. It installs nothing.

### Restore

1. `ref` is resolved as: exact full name, then a unique `/`-suffix (`heads/main`, `tags/v1.0.0`),
   then a unique leaf name. An ambiguous name lists the candidates. A unique abbreviated commit
   hash of 7 or more hex characters also works.
2. Branches become local branches. Tags stay tags, including annotated ones. Anything else is a
   detached checkout.
3. A ref missing from the bundle but present as a release degrades: commit, then default branch,
   then assets only. Each step prints a warning. `--strict` turns degradation into a failure.
4. Releases-only archives restore their assets into `<dest>/assets/`.
5. Only assets that were stored are restored. `--fetch-missing` downloads the rest.
6. A non-empty destination is refused unless `--force`. The filesystem root, your home directory
   and the current directory are never accepted.

### Verify

`verify` checks that metadata matches the disk: the bundle chain replays in a scratch clone, ref
counts agree, the release index parses, recorded assets exist at the recorded size, `state.json`
does not contradict `meta.json`, and `history.jsonl` has no broken lines. Errors exit 1. Warnings do
not, unless `--strict`. `--json` prints the report for scripts.

### Archive location

```bash
repoArchiver archive ./checkout --dir /data/archives/requests     # one archive elsewhere
repoArchiver --archives-dir /mnt/backup/archives archive <url>    # different root for one run
repoArchiver restore /data/archives/requests v2.32.3 -d out       # path instead of an id
```

Every command that takes an archive id also accepts a path. `sync`, `archives` and `verify` without
arguments only scan the archives root, so pass the path for archives stored elsewhere.

### Progress

Long steps announce themselves, git's progress output is forwarded, downloads draw a bar, and a
heartbeat appears after five idle seconds.

```
→ archiving code from https://github.com/psf/requests
→ cloning into a work mirror
  Receiving objects 42%
→ writing code.bundle
  Writing objects 100%
archived "psf__requests" -> archives/psf__requests
  refs: 312  releases: 48  bundle: 84.3 MiB  assets: 96
```

| Flag | Behaviour |
|---|---|
| `--progress auto` (default) | bars and heartbeats on a terminal; silent when redirected |
| `--progress always` | also report stages and milestones when stderr is a pipe |
| `--progress never`, `-q` | only the final summary |
| `--verbose` | also forward raw git output |

Progress goes to stderr. stdout carries results only.

### Language

The interface ships in English and Simplified Chinese:

```bash
repoArchiver --lang zh archives
REPOARCHIVER_LANG=zh repoArchiver verify --all
```

Precedence: `--lang`, then `language` in the config, then `$REPOARCHIVER_LANG` or the locale, then
English. Messages live in `repoArchiver/i18n.py`; see [CONTRIBUTING](CONTRIBUTING.md) to add one.

## Configuration

Optional. The default path is `~/.repoArchiver/config.toml`; a commented sample is in
[`examples/config.toml`](examples/config.toml).

```toml
archives_dir = "archives"   # relative paths resolve against the working directory
language = "en"             # en | zh
progress = "auto"           # auto | always | never
trace = true                # refresh tracked archives before reading commands
trace_interval = "6h"       # throttle the on-launch trace; 0 = always

[tokens]                    # per-host tokens; "*" is the fallback
"github.com" = "ghp_xxx"

retries = 3                 # 5xx / 408 / 425 / 429 and transport errors
timeout = 60                # seconds per request
min_interval = 0            # politeness delay per host, in seconds
resume = true               # resume interrupted downloads
```

Environment fallbacks for tokens: `GITHUB_TOKEN`/`GH_TOKEN`, `GITLAB_TOKEN`,
`GITEA_TOKEN`/`CODEBERG_TOKEN`, `BITBUCKET_TOKEN`/`BITBUCKET_APP_PASSWORD`. `-c/--config PATH`
points at another file, which must exist; a missing default config means defaults are used.

## Exit codes

| Code | Meaning |
|---|---|
| `0` | success; degradation, warnings and failed assets are still success |
| `1` | expected failure: config, archive, git, ref, network; also `sync` failures and `verify` errors |
| `2` | command-line usage error |

## Not implemented

- automatic compaction of long incremental chains (`compact` is manual)
- shallow archives (`--depth`): bundles cannot be shallow, a different layout would be needed
- content checksums for assets (`verify` compares sizes)
- concurrent asset downloads
- rewriting `local_path` when an archive directory is moved

See [docs/ROADMAP.md](docs/ROADMAP.md) for the design trade-offs.

## Development

```bash
python -m unittest discover -s tests -v
python -m ruff check repoArchiver tests
```

The tests use the standard library only and never touch the network. They build throwaway local
repositories, synthesize archive directories and stub the platform APIs, the HTTP layer and the
download step. Temporary directories are created in `.test-tmp/` inside the repository (override
with `REPOARCHIVER_TEST_TMP`), so the suite also runs where the system temp directory is restricted.

Module map:

```
repoArchiver/
├─ cli.py          argparse, subcommand dispatch, global options
├─ archiver.py     archive pipeline (archive / sync share it)
├─ platforms/      github / gitlab / gitea / bitbucket release adapters
├─ http.py         retries, rate limits, resumable downloads
├─ releases.py     release index and asset downloads
├─ bundle.py       git subprocess and bundle wrappers
├─ chain.py        incremental bundle chains
├─ rewrite.py      cutoff-based history rewriting
├─ cutoff.py       cutoff parsing, ref selection, snapshots
├─ refs.py         ref include/exclude, orphan pins
├─ tags.py         annotated tag reading and rebuilding
├─ restore.py      restore logic and ref resolution
├─ track.py        sync / trace
├─ verify.py       consistency checks
├─ schedule.py     scheduler snippets and trace throttling
├─ selection.py    release / asset selectors
├─ layout.py       config, paths, JSON metadata
├─ progress.py     stage reporting, git progress, download bars
├─ i18n.py         message catalogue (en / zh)
└─ errors.py       exceptions and warning output
```

## License

MIT. See [LICENSE](LICENSE).
