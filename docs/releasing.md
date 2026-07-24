# Releases

Use Conventional Commits (`feat:`, `fix:`, `docs:`, `test:`, `refactor:`,
`build:`, `ci:`, and `chore:`). Breaking changes use `!` or a
`BREAKING CHANGE:` footer.

`pyproject.toml` is the repository-wide version source. Runtime version output
uses installed package metadata, and python-semantic-release updates the
project version, changelog, tag, and GitHub release.

Pull requests run formatting, lint, strict typing, tests with coverage, build,
container build, and secret scan. The release job is gated on those jobs and
runs only for a push to `main`. Configure `main` as a protected branch and
require `checks` and `secret-scan`; releases are never published from pull
requests. The workflow needs repository `contents: write` only in its release
job.
