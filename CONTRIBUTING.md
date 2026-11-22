# Contributing to Keel

Thanks for helping. Bug reports, statement formats from banks Keel doesn't read yet,
translations, docs and code are all welcome.

## Getting started

```bash
git clone https://github.com/realAllenSong/Keel.git
cd Keel
make hooks       # privacy and lint checks before each commit
make seed-demo   # a synthetic household to work on
make test
```

`make dev-demo` runs the engine on the demo ledger and `make run` the app from source against it.

## Never commit real financial data

Tests and fixtures use made-up statements only (`fixtures/`). Never commit a real statement, a
ledger file, account or card numbers, names or amounts from your own accounts, or any key. The
pre-commit hook refuses card and ID numbers, but it can't catch everything: check your diff.

A new bank's statement layout usually needs only a YAML profile in
`backend/pf/importers/profiles/` plus a small synthetic sample in `fixtures/`. Describe the
real file's columns in the pull request instead of attaching it.

## Pull requests

- Open an issue first for larger changes, so we can agree on the approach.
- Keep each pull request focused, with tests for what it changes (`make test` passes).
- Interface text is written in Chinese and translated for English: add the English to
  `apple/Sources/KeelCore/L10nEN.swift` or `backend/pf/i18n_en.py` (the checks fail otherwise).
- Say what the change does and why.

## License

By contributing you agree that your changes are released under the
[GNU AGPL v3](LICENSE) that covers the project, with the attribution terms in [NOTICE](NOTICE).
You keep the copyright to your contribution.

## Questions

Open an issue on GitHub.
