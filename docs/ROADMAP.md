# Roadmap and design notes

## Done

- `code.bundle` archives with full history, snapshots (`--snapshot`) and cutoff rewriting
  (`--rewrite-history`).
- Incremental chains (`--incremental`) and `compact`.
- Real ref pruning (`--include-ref` / `--exclude-ref`) and orphan preservation (`--keep-orphans`).
- Release metadata and asset downloads for GitHub, GitLab, Gitea/Forgejo and Bitbucket.
- Release and asset selectors, persisted so `sync` reuses them.
- Retries with backoff, rate-limit handling, per-host delays and resumable downloads.
- Tracking (`-t/--tracker`), `sync`, on-launch `trace`, `trace_interval`, `schedule`.
- `verify` consistency checks and `history.jsonl`.
- Progress reporting, English/Chinese interface, archives outside the default root.

## Trade-offs

**Rewriting history changes every hash.** A bundle must be self-contained, and a shallow history
cannot be cloned. Keeping "history since T" with unchanged hashes is therefore not possible; the
three strategies above are what is left.

**Incremental chains only grow.** A delta can add refs and advance them. Deleting a ref, or
force-pushing it backwards, cannot be expressed, so the base is rebuilt. Long chains also make every
`restore` replay more fetches, which is why `compact` exists.

**`--keep-orphans` mostly matters for local sources.** A mirror clone transfers refs only, so a
remote usually has no unreachable objects to preserve.

**Bitbucket downloads have no version.** They live at repository level, so they are stored as one
entry without a date and are skipped by `--since`.

**`verify` compares sizes, not content.** A truncated file is caught; a same-sized corruption is
not. Content manifests are not implemented.

**Selectors never hide metadata.** Excluded releases and assets stay in `index.json` with
`local_path: null`, so `sync` can fetch them later. Ref rules are different: excluded refs are not
stored at all.

## Not implemented

- automatic compaction of long chains
- shallow archives (`--depth`)
- content checksums for assets
- concurrent asset downloads
- rewriting `local_path` when an archive directory moves
- a migration command for archives produced by 1.x (reading them works, they are not rewritten)

## Ideas, not commitments

- `archive --mirror-only` that skips metadata for speed
- a plugin hook for forges that are not GitHub/GitLab/Gitea/Bitbucket
- `verify --fix` for the cheap cases (stale counts, missing index entries)
