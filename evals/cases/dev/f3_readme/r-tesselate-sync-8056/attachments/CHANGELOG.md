# Changelog

## 0.3.5

- Removed `--threads N`, deprecated since 0.2.0. Use `--workers N` instead.
- Added `--dry-run` to print what would happen without changing anything.
- Fixed a crash when SOURCE contained a symlink loop.

## 0.2.3

- `tesselatesync` now reads `./tesselatesync.toml` by default.
- `--threads N` still works but prints a deprecation warning.

## 0.2.0

- Deprecated `--threads N` in favour of `--workers N`.
