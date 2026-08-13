# Dependency license inventory

Generated from the CycloneDX documents in `dist/` (`sbom-python.cdx.json`,
`sbom-frontend.cdx.json`). Re-run `bash scripts/generate_sbom.sh` then
`python scripts/generate_licenses_inventory.py` after a dependency change.
Those JSON files stay out of git. Generators: `cyclonedx-py` 7.3.1
(`cyclonedx-bom`) and `@cyclonedx/cyclonedx-npm` 4.0.0.

Python rows are the resolved install of `retinue[server]` in a throwaway
environment (the extra a hub needs). Frontend rows are the npm production
tree (`--omit dev`): `react`, `react-dom`, `lucide-react`, and whatever they
pull in. Test-only extras (`pytest`) and panel build tools (`vite`,
`typescript`) are not listed.

Classes: **permissive** (MIT, Apache-2.0, BSD, ISC, and similar),
**weak-copyleft** (MPL, LGPL, EPL), **restricted** (GPL, AGPL, SSPL, BUSL),
**unknown** / **review** (no SPDX id, or a name that is not on the lists
above). Dual licenses joined with OR are treated as permissive when at least
one option is permissive and none is restricted.

## Conclusion

No GPL, AGPL, SSPL, or BUSL license appears in the resolved production set. Flagged: certifi. certifi is MPL-2.0 (weak copyleft, file-level). That does not block a community-preview tag; re-check the flagged rows when versions move.

## Flagged rows

| Package | Version | License | Class |
|---|---|---|---|
| certifi | 2026.7.22 | MPL-2.0 | weak-copyleft |

## Python (`retinue[server]`)

| Package | Version | License | Class |
|---|---|---|---|
| annotated-doc | 0.0.5 | MIT | permissive |
| annotated-types | 0.8.0 | MIT | permissive |
| anyio | 4.14.2 | MIT | permissive |
| attrs | 26.1.0 | MIT | permissive |
| cffi | 2.1.1 | MIT-0 | permissive |
| click | 8.4.2 | BSD-3-Clause | permissive |
| cryptography | 50.0.0 | Apache-2.0 OR BSD-3-Clause | permissive |
| fastapi | 0.141.1 | MIT | permissive |
| greenlet | 3.5.5 | MIT AND PSF-2.0 | permissive |
| h11 | 0.16.0 | MIT | permissive |
| httpcore | 1.0.9 | BSD-3-Clause | permissive |
| httptools | 0.8.0 | MIT | permissive |
| httpx | 0.28.1 | BSD-3-Clause | permissive |
| httpx-sse | 0.4.3 | MIT | permissive |
| idna | 3.18 | BSD-3-Clause | permissive |
| jsonschema | 4.26.0 | MIT | permissive |
| jsonschema-specifications | 2025.9.1 | MIT | permissive |
| mcp | 1.29.0 | MIT | permissive |
| pip | 26.2.1 | MIT | permissive |
| pycparser | 3.0 | BSD-3-Clause | permissive |
| pydantic | 2.13.4 | MIT | permissive |
| pydantic-settings | 2.15.0 | MIT | permissive |
| pydantic_core | 2.46.4 | MIT | permissive |
| PyJWT | 2.13.0 | MIT | permissive |
| python-dotenv | 1.2.2 | BSD-3-Clause | permissive |
| python-multipart | 0.0.32 | Apache-2.0 | permissive |
| PyYAML | 6.0.3 | MIT | permissive |
| referencing | 0.37.0 | MIT | permissive |
| rpds-py | 2026.6.3 | MIT | permissive |
| SQLAlchemy | 2.0.52 | MIT | permissive |
| sse-starlette | 3.4.8 | BSD-3-Clause | permissive |
| starlette | 1.6.0 | BSD-3-Clause | permissive |
| typing-inspection | 0.4.4 | MIT | permissive |
| typing_extensions | 4.16.0 | PSF-2.0 | permissive |
| uvicorn | 0.52.1 | BSD-3-Clause | permissive |
| uvloop | 0.22.1 | MIT | permissive |
| watchfiles | 1.2.0 | MIT | permissive |
| websockets | 17.0.1 | BSD-3-Clause | permissive |
| certifi | 2026.7.22 | MPL-2.0 | weak-copyleft |

## Frontend (npm production)

| Package | Version | License | Class |
|---|---|---|---|
| js-tokens | 4.0.0 | MIT | permissive |
| loose-envify | 1.4.0 | MIT | permissive |
| lucide-react | 0.460.0 | ISC | permissive |
| react | 18.3.1 | MIT | permissive |
| react-dom | 18.3.1 | MIT | permissive |
| scheduler | 0.23.2 | MIT | permissive |
