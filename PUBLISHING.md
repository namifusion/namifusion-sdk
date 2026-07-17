# Publishing

Steps to publish both packages to npm and PyPI, plus a one-time checklist
for the first release.

## npm (`@namifusion/client`)

1. Create the `namifusion` npm organization (if it doesn't exist yet):
   https://www.npmjs.com/org/create — the package name `@namifusion/client`
   is scoped under it.
2. Log in locally: `npm login`.
3. From `packages/typescript/`:
   ```sh
   npm ci
   npm run typecheck
   npm test
   npm run build
   ```
4. Verify the published file listing before publishing — `npm pack
   --dry-run` and confirm the file list includes `LICENSE` (alongside
   `dist/` and `README.md`; npm includes `LICENSE` automatically without
   needing an entry in `package.json`'s `files` array).
5. Publish (scoped packages default to private, so `--access public` is
   required the first time):
   ```sh
   npm publish --access public
   ```
6. For subsequent releases: bump `version` in `packages/typescript/package.json`
   **and** `SDK_VERSION` in `packages/typescript/src/client.ts` (keep the two in
   sync), repeat steps 3–5 (omit `--access public` once the package is public —
   it's harmless to keep passing it either way).

## PyPI (`namifusion`)

1. Create a PyPI account and generate an API token: https://pypi.org/manage/account/#api-tokens
   (scope it to the `namifusion` project once it exists, or to your whole
   account for the first upload).
2. Configure the token, e.g. via `~/.pypirc`:
   ```ini
   [pypi]
   username = __token__
   password = pypi-...
   ```
   or export `TWINE_USERNAME=__token__` / `TWINE_PASSWORD=pypi-...` in the
   environment instead.
3. From `packages/python/`, run the tests, then build the sdist + wheel:
   ```sh
   python -m pip install --upgrade build twine
   pytest
   python -m build
   ```
4. Verify the built artifacts before uploading: `twine check dist/*` (checks
   package metadata and that the README renders correctly on PyPI).
5. Upload:
   ```sh
   twine upload dist/*
   ```
6. For subsequent releases: bump `version` in `packages/python/pyproject.toml`,
   `__version__` in `packages/python/src/namifusion/__init__.py`, **and**
   `_SDK_VERSION` in `packages/python/src/namifusion/_client.py` (keep all
   three in sync), clear `dist/` and repeat steps 3–5.

## First-release checklist

Do these once, before the very first `npm publish` / `twine upload`:

- [ ] Replace the placeholder GitHub URL (`https://github.com/shanweimu/namifusion-sdk`)
      with the real repository URL once the org/repo is created. It appears in:
  - `packages/typescript/package.json` (`repository.url`)
  - `packages/python/pyproject.toml` (`[project.urls]` — `Homepage`, `Repository`)
  - `README.md` (repository link)
- [ ] Confirm the copyright year in [`LICENSE`](LICENSE) is still correct.
- [ ] Tag the release: `git tag v0.1.0 && git push origin v0.1.0`.
