# GUI End-to-End Tests (Playwright)

Browser-driven tests for the Admin GUI (`/`), the Visu SPA (`/visu`), and the demo build.
They drive a real Chromium against a running open bridge server instance.

## Prerequisites

1. Backend stack reachable on the resolved base URL (see below).
2. Node.js 20.19+ or 22+, the version pinned by the repo's `.nvmrc`.
3. Browser binaries installed once after `npm ci`:
   ```bash
   npx playwright install --with-deps
   ```
   (On macOS/Windows you can usually drop `--with-deps`.)

## Resolving the base URL

`playwright.config.ts` resolves the URL in this order:

1. `BASE_URL` environment variable (explicit override, wins everything).
2. `OBS_HTTP_HOST_PORT` from the repository-root `.env` (the same file
   `docker compose up` reads). The base URL becomes `http://localhost:${OBS_HTTP_HOST_PORT}`.
3. Built-in default `http://localhost:8080`.

Most contributors don't need to set `BASE_URL` manually — copy `.env.example`
to `.env` at the repo root, edit `OBS_HTTP_HOST_PORT` if you publish on a
non-default port (e.g. `8082` for a parallel dev stack), and Playwright follows.

## Running

From the repository root:

```bash
# All projects
npm --prefix tests/gui test

# Interactive UI mode
npm --prefix tests/gui run test:ui

# Single project
cd tests/gui && npx playwright test --project=admin

# Single spec
cd tests/gui && npx playwright test admin/ringbuffer.spec.ts
```

Default credentials for `auth.setup.ts` are `admin / admin`; override with
`E2E_USER` / `E2E_PASS` if your instance has them changed.

## Common failure modes

| Symptom | Likely cause | Fix |
|---|---|---|
| `auth.setup.ts` times out waiting for `[data-testid="input-username"]` | Backend not running, or running on a port the runner can't reach | Verify `curl ${BASE_URL}/api/v1/system/health` returns 200. Check `OBS_HTTP_HOST_PORT` in `.env`. |
| `Login failed: 401` | Wrong credentials | Set `E2E_USER` / `E2E_PASS`. |
| `Browser executable not found` | `npx playwright install` never ran | Re-run `npx playwright install --with-deps` in `tests/gui/`. |
| Tests pass locally but the saved storage state points at the wrong port | A previous run wrote `.auth/admin.json` with a different `BASE_URL` | Delete `tests/gui/.auth/` and re-run. |

## Files

- `playwright.config.ts` — project layout (admin/visu/demo), base URL resolution.
- `auth.setup.ts` / `demo.setup.ts` — log in once per project, persist to `.auth/*.json`.
- `helpers.ts` — token cache + REST helpers; exports the resolved `BASE_URL`.
- `admin/`, `visu/`, `demo/` — actual spec files.

## CI

These tests do **not** run on GitHub CI today. They are validated only by
contributor machines. A dedicated workflow is tracked separately.
