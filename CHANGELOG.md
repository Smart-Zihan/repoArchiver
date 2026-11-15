# Changelog

All notable changes to this project are documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## 0.1.0 - 2026-10-31

First release.

### Added

- `archive` stores a git repository as `code.bundle` plus release metadata and, on request,
  release assets. Scopes: `--only-code`, `--only-releases`.
- Cutoffs with `--since` / `--since-tag`. `--snapshot` keeps one root commit per ref;
  `--rewrite-history` keeps the commits at/after the cutoff as commits.
- Incremental bundle chains (`--incremental`) with `compact`.
- Ref selection (`--include-ref` / `--exclude-ref`) and orphan preservation (`--keep-orphans`).
- Release adapters for GitHub, GitLab, Gitea/Forgejo and Bitbucket, with retries and backoff,
  rate-limit handling and resumable downloads.
- `list`, `restore`, `inspect`, `archives`, `verify`, `sync`, `compact` and `schedule`.
- Tracking with `-t/--tracker`, on-launch `trace`, `trace_interval` and a `history.jsonl` log.
- Progress output (`--progress auto|always|never`, `-q`, `--verbose`) and English / Simplified
  Chinese messages (`--lang`, `$REPOARCHIVER_LANG`).
- `--archives-dir` and `archive --dir` for archives outside the configured root.

[0.1.0]: https://github.com/Smart-Zihan/repoArchiver/releases/tag/v0.1.0
