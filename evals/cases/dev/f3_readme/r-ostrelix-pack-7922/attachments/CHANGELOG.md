# Changelog

## 0.8.5

- Removed `--threads N`, deprecated since 0.7.0. Use `--workers N` instead.
- Added `--dry-run` to print what would happen without changing anything.
- Fixed a crash when SOURCE contained a symlink loop.

## 0.7.2

- `ostrelixpack` now reads `./ostrelixpack.toml` by default.
- `--threads N` still works but prints a deprecation warning.

## 0.7.0

- Deprecated `--threads N` in favour of `--workers N`.
