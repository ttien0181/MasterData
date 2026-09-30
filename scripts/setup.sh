#!/bin/bash
# Setup script cho AI20K project

set -e

echo "=== AI20K Project Setup ==="

# uv manages Python; no system Python installation is required.
uv venv .venv
uv pip install --python .venv -r requirements.txt

# Create .env if not exists
if [ ! -f .env ]; then
    cp .env.example .env
    echo "Created .env — please edit with your API keys"
fi

# Create data directories
mkdir -p data/chroma

uv run --python .venv python -m masterone.mdm.migrate
echo "Setup complete! Run: uv run --python .venv python -m uvicorn masterone.main:app --reload --port 8001"
