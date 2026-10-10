# Deploy backend on Render and frontend on Vercel

This setup uses the default `*.onrender.com` and `*.vercel.app` domains. The browser calls `/api/*` on Vercel; Next.js rewrites those requests to Render. The existing host-only `SameSite=Lax` session cookie can therefore stay unchanged.

## Render: backend

Create a **Web Service** from this repository:

| Setting | Value |
| --- | --- |
| Root Directory | `backend` |
| Language | Python 3 |
| Build Command | `pip install .` |
| Start Command | `uvicorn masterone.main:app --host 0.0.0.0 --port $PORT` |
| Health Check Path | `/health` |

Set these environment variables in Render:

| Variable | Value |
| --- | --- |
| `PYTHON_VERSION` | `3.11.11` |
| `APP_ENV` | `production` |
| `CORS_ORIGINS` | Exact production frontend origin, e.g. `https://your-app.vercel.app` (no trailing slash) |
| `AUTH_CSRF_SECRET` | Stable random secret, at least 32 characters |
| `MDM_DATABASE_PROFILE` | `neon` (the code uses this profile for any hosted PostgreSQL, including Supabase) |
| `MDM_DATABASE_URL` | Supabase connection string using the `postgresql+psycopg://` scheme and `sslmode=require` |
| `R2_ENDPOINT_URL` | `https://<account-id>.r2.cloudflarestorage.com` |
| `R2_ACCESS_KEY_ID` | R2 access key ID |
| `R2_SECRET_ACCESS_KEY` | R2 secret access key |
| `R2_BUCKET` | Bucket name |
| `LLM_ENABLED` | `false` unless OpenRouter is configured |

Get the database URL from Supabase **Connect**. Use the direct connection if Render can reach its IPv6 address; otherwise choose the **Session pooler** on port `5432`. Replace `postgresql://` with `postgresql+psycopg://` and add `?sslmode=require` (or `&sslmode=require` if the URL already has query parameters). Percent-encode special characters in the password. Do not use the transaction pooler on port `6543` with the current database driver configuration. Set `OPENROUTER_API_KEY` if `LLM_ENABLED=true`.

The backend upgrades the database schema when it starts. Do not rely on the repository's development `.env.example` values for production.

## Vercel: frontend

Import the same repository as a **separate Vercel project** with Root Directory `frontend` and framework **Next.js**. Set:

| Variable | Value |
| --- | --- |
| `RENDER_BACKEND_URL` | Backend origin, e.g. `https://your-api.onrender.com` (no `/api` path or trailing slash) |

Do **not** set `NEXT_PUBLIC_API_BASE_URL` for this deployment. If it was previously set to the Render URL, delete it and redeploy. In production, the frontend now calls its own origin; `next.config.ts` forwards `/api/*` and `/health` to Render. The Vercel build fails with a clear error if `RENDER_BACKEND_URL` is missing.

## Check after deployment

1. Open `https://your-api.onrender.com/health`, then `https://your-app.vercel.app/health`; both should return `{"status":"ok","env":"production"}`.
2. Register or log in on the Vercel site. In browser Network, API requests should go to `your-app.vercel.app/api/...`, and `/api/v1/auth/me` should return the signed-in user after refresh.
3. Try an upload and CSV export. The existing download of an export stored in R2 redirects to a signed R2 URL and may require a separate browser download change; test this path before relying on it. Direct browser access to R2 presigned URLs requires a bucket CORS policy for the Vercel origin.

For preview deployments, add each preview origin to `CORS_ORIGINS` (comma-separated) if you need login on preview URLs. Keep R2 credentials, the database URL, and `AUTH_CSRF_SECRET` only on Render.
