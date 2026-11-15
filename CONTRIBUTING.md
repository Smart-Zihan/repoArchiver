# Contributing

Thanks for taking the time to report a bug, suggest a change or send a patch. This project is
small on purpose: it uses only the Python standard library plus the `git` executable, and it
values predictable behaviour over features.

## Getting started

```bash
git clone <your fork>
cd repoArchiver
python -m unittest discover -s tests -v      # full suite
python -m repoArchiver --help                # run from the source tree
```

No virtualenv or dependency install is required to run the tests. `pip install -e '.[dev]'` adds
`ruff` and `build` if you want the linters.

## Project layout

- `repoArchiver/` — the package (see the module map in the README).
- `tests/` — `unittest` modules; `tests/support.py` holds the temp-directory and git helpers.
- `examples/config.toml` — commented sample configuration.
- `docs/ROADMAP.md` — agreed-upon future work and known trade-offs.

## Ground rules

1. **No new runtime dependencies.** If a feature needs one, it is probably out of scope.
2. **Warnings never abort.** Expected problems are reported with `warn()`; only real failures
   raise `RepoArchiverError` subclasses and set a non-zero exit code.
3. **Tests must not touch the network.** Stub the platform layer, the HTTP layer or the download
   step, as the existing tests do.
4. **Keep archives readable by older versions.** New `meta.json` fields are additive; missing
   fields must keep working (see how `bundle_chain` degrades to a single bundle).
5. **Document behaviour changes** in `README.md`, `README.zh-CN.md` and `CHANGELOG.md`.

## Localization

User-facing English text is the message id, so a translation is never required for the code to
work:

```python
from .i18n import tr

warn(tr('archive "{archive_id}" not found', archive_id=name))
```

- Wrap new prose in `tr()`; keep purely technical diagnostics (config-schema complaints, raw git
  or HTTP detail) in English.
- Add the Chinese translation to `_catalog_zh()` in `repoArchiver/i18n.py`, keeping the
  `{placeholder}` names identical.
- To add a language: extend `LANGUAGES`, `LANGUAGE_LABELS` and `_ALIASES`, add a catalog
  function, and register it in `_CATALOG`. Placeholders are checked at runtime — a mismatch
  falls back to English rather than crashing.
- `--lang` wins over the `language` config key, which wins over `$REPOARCHIVER_LANG`/locale.

## Progress output

Anything that can take more than a second should say so:

```python
from . import progress

progress.stage(tr("fetching release metadata from {source}", source=src))   # banner
with progress.long_stage(tr("writing {name}", name=path.name)):             # banner + heartbeat
    ...
```

`git()` in `bundle.py` streams output when called with `stream=True`; do that only for commands
whose stdout is not parsed (clone, fetch, bundle). Downloads accept an `on_progress` callback.
Keep stdout reserved for results — progress goes to stderr.

## Commit and PR checklist

- [ ] Tests pass locally and new behaviour has a test.
- [ ] `python -m ruff check repoArchiver tests` is clean (if ruff is installed).
- [ ] Public behaviour changes are reflected in both READMEs and `CHANGELOG.md`.
- [ ] New user-facing strings go through `tr()` and have a Chinese entry.
- [ ] No tokens, archive contents or personal paths in the diff.

Small, focused commits with an imperative subject line ("add --dir for archives outside the
root") are easiest to review.
