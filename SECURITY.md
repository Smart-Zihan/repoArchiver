# Security policy

## Supported versions

The latest released minor version receives fixes. Older versions are expected to be upgraded.

## Reporting a vulnerability

Please **do not** open a public issue for a security problem. Report it privately through the
repository's *Security → Advisories → Report a vulnerability* form, or by contacting the
maintainer directly if advisories are not enabled.

Include the version, operating system, the exact command line, and what you observed. A
reproduction with a throwaway local repository is ideal. You can expect an initial reply within a
week.

## How repoArchiver handles secrets

- **Tokens are read, never written.** They come from the config file, a `[tokens]` table or the
  environment (`GITHUB_TOKEN`, `GITLAB_TOKEN`, `GITEA_TOKEN`, `BITBUCKET_TOKEN`, …). They are
  attached to outgoing HTTP requests only and are never stored in `meta.json`, `state.json`,
  `history.jsonl` or log output.
- **Tokens are redacted from errors.** HTTP error messages carry the status and the server's
  message, not the request headers.
- **Config files live outside the archive.** The default location is `~/.repoArchiver/config.toml`;
  keep it readable only by you (`chmod 600`). Archives themselves contain no credentials, so they
  can be copied to a backup medium as-is.
- **Archives may contain private code and release assets.** Treat an archive directory with the
  same care as the source repository; do not publish archives of private repositories.
- **Archived content is not executed.** `repoArchiver` only runs `git`, and only against
  repositories you point it at. Release assets are downloaded and stored, never opened.

## Network behaviour worth knowing

`repoArchiver` contacts only the source host you name (its release API and asset URLs) and
`codeload.github.com` for GitHub source archives. There is no telemetry, no update check and no
other outbound traffic. `--only-code` skips the release API entirely; `--only-releases` skips git
entirely.
