# E5 Linux CPU runtime

This runtime uses the same immutable `intfloat/multilingual-e5-small` model revision and model-manifest hashes as the Windows trial. It runs in a separate Python 3.11 Linux venv inside the existing backend/worker image. `requirements-linux.txt` is LF-only and its SHA-256 is checked by both supervisor and encoder worker.

Prepare weights on the host before starting the existing Compose stack:

```powershell
# If the approved model is not already present:
.\.venv\Scripts\python.exe scripts/fetch_m2a_e5.py
.\.venv\Scripts\python.exe scripts/prepare_encoder_linux_assets.py
docker compose build backend worker
docker compose up -d --no-deps --force-recreate backend worker
```

The existing Compose file mounts `data/embedding/e5-cpu-linux/model` read-only at `/opt/encoder-runtime/model`. No customer files or API credentials are baked into the encoder image. Downloaded public wheels in `wheels/` are ignored by Git; a complete wheelhouse permits offline installation, otherwise the Docker build downloads the exact pinned versions. No inference-time model download or model substitution is allowed.

Enablement still requires a validated owner-scoped hybrid profile. Existing runs retain immutable bindings. The QA activation script applies new defaults only after successful live four-position validation and saves previous default pointers in its report.

Accounting records durable batch intents and results without storing text in usage receipts. Token counts include the E5 prefix and special tokens after tokenizer truncation. Failed/timed-out attempts can have unknown token usage and are reported explicitly. Vector cache hits perform no model request and incur no embedding API charge. CPU/hosting/electricity are separate, unmeasured costs.
