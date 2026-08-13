#!/usr/bin/env bash
# Build CycloneDX SBOMs for the production Python install and the panel.
# Tools: cyclonedx-bom (worktree venv) and @cyclonedx/cyclonedx-npm (npx).
# Output lands in dist/ and is not committed.
set -euo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cd "$ROOT"

if ! python -m cyclonedx_py --version >/dev/null 2>&1; then
  echo "generate_sbom: install cyclonedx-bom in the worktree venv:" >&2
  echo "  pip install 'cyclonedx-bom>=6,<8'" >&2
  exit 2
fi

mkdir -p dist
python_out="$ROOT/dist/sbom-python.cdx.json"
frontend_out="$ROOT/dist/sbom-frontend.cdx.json"

sbom_venv=$(mktemp -d)
cleanup() { rm -rf "$sbom_venv"; }
trap cleanup EXIT

python3 -m venv "$sbom_venv"
"$sbom_venv/bin/python" -m pip install --upgrade pip
"$sbom_venv/bin/python" -m pip install "${ROOT}[server]"

python -m cyclonedx_py environment "$sbom_venv" \
  --pyproject "$ROOT/pyproject.toml" \
  --mc-type application \
  --of JSON \
  --output-reproducible \
  -o "$python_out"

if [ ! -d "$ROOT/webui/node_modules" ]; then
  (cd "$ROOT/webui" && npm ci --no-audit --no-fund)
fi

(
  cd "$ROOT/webui"
  npx --yes @cyclonedx/cyclonedx-npm@4.0.0 \
    --omit dev \
    --package-lock-only \
    --output-format JSON \
    --output-file "$frontend_out" \
    package.json
)

echo "wrote $python_out"
echo "wrote $frontend_out"
python -m cyclonedx_py --version
npx --yes @cyclonedx/cyclonedx-npm@4.0.0 --version 2>/dev/null || true
