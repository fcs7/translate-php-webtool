# CLAUDE.md — Trans-Script Web

## Project Overview

Trans-Script Web is a web application for automatic translation of PHP localization files (EN → PT-BR). Users upload `.zip`, `.rar`, or `.tar.gz` archives (or raw `.php` files) containing PHP `$msg_arr` arrays, and the system translates strings in batch with real-time WebSocket progress, outputting translated ZIP + VoipNow TAR packages.

**Primary language of the codebase**: Portuguese (BR) — all comments, commit messages, variable names in some places, UI text, and documentation are in Brazilian Portuguese.

## Architecture

```
Frontend (React 19 + Vite + Tailwind)
    ↕ REST API + WebSocket (Socket.IO)
Backend (Flask + Gunicorn/gevent)
    ├── app.py           — Flask routes, WebSocket handlers, SPA serving
    ├── translator.py    — Job orchestration, file processing, batch translation
    ├── translate.py     — CLI translation script + regex/helper functions
    ├── auth.py          — Auth (password + OTP), SQLite DB, cache, quota
    ├── admin_auth.py    — Admin sessions (AES-256-GCM + HMAC-SHA256)
    ├── config.py        — Centralized configuration
    └── engine/
        ├── __init__.py  — Singleton factory, provider chain assembly
        ├── engine.py    — TranslationEngine: cache + fallback chain
        ├── base.py      — TranslationProvider abstract base class
        ├── cache.py     — TwoLevelCache (LRU memory + SQLite)
        └── providers/
            ├── google_free.py     — Google Translate via HTTP (primary)
            ├── deepl_free.py      — DeepL Free API (requires API key)
            ├── mymemory.py        — MyMemory API (zero-dependency fallback)
            └── translate_shell.py — translate-shell CLI wrapper (last resort)
```

### Translation Flow

1. User uploads file(s) via `/api/upload`
2. Backend extracts archive → `backend/jobs/{job_id}/input/`
3. Background thread starts (`translator._run`)
4. For each PHP file (parallel, up to 4 workers):
   - Pass 1: Collect translatable `$msg_arr` strings
   - Pass 2: Translate in batches of 100 via engine (cache → provider chain)
   - Pass 3: Write translated file
5. Creates `output.zip` + `voipnow.tar.gz`
6. Emits `translation_complete` WebSocket event
7. Updates user storage quota

### Provider Chain (Chain of Responsibility)

Order: GoogleFree → DeepL (if API key set) → MyMemory → TranslateShell

All providers use only Python stdlib (`urllib`) — no `requests`/`httpx` dependency.

### Database

Single SQLite file (`backend/users.db`) with tables:
- `users` — accounts with password hash, storage quota
- `translation_cache` — persistent translation cache with hit counts
- `activity_log` — user action audit trail
- `job_history` — completed job records with expiration
- `jobs` — active/recent job state
- `admin_sessions` — encrypted admin session tokens

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Backend | Python 3.10+, Flask 3.1, Flask-SocketIO 5.5, Gunicorn (gevent) |
| Frontend | React 19, Vite 6, Tailwind CSS 3.4, Socket.IO Client 4.8 |
| Database | SQLite (single file, thread-safe with locks) |
| Auth | Password (scrypt via werkzeug) + OTP via email |
| Admin | AES-256-GCM encrypted sessions, HMAC-SHA256 token signing |
| Deploy | Nginx reverse proxy, systemd service, deploy.sh script |

## Project Structure

```
translate-php-tool/
├── backend/
│   ├── app.py              # Flask app: routes + WebSocket + SPA serve
│   ├── translator.py       # Job model, translation orchestration
│   ├── translate.py        # CLI script + regex patterns + helpers
│   ├── auth.py             # Auth, DB init, cache, quota, job history
│   ├── admin_auth.py       # Admin crypto sessions (HKDF + AES-GCM)
│   ├── config.py           # All configuration constants
│   ├── wsgi.py             # Gunicorn entry point
│   ├── __init__.py
│   ├── requirements.txt    # Python dependencies
│   └── engine/             # Translation engine module
│       ├── __init__.py     # Singleton factory
│       ├── engine.py       # TranslationEngine class
│       ├── base.py         # TranslationProvider ABC
│       ├── cache.py        # TwoLevelCache (memory LRU + SQLite)
│       └── providers/      # Translation provider implementations
├── frontend/
│   ├── src/
│   │   ├── App.jsx         # Root component, routing, job state
│   │   ├── main.jsx        # React entry point
│   │   ├── index.css       # Tailwind + custom styles
│   │   ├── components/     # FileUpload, TranslationProgress, UserHistory, Header
│   │   ├── pages/          # LoginPage, AdminPanel
│   │   ├── hooks/          # useAuth, useSocket
│   │   ├── services/       # api.js (REST client), cache.js
│   │   └── utils/          # formatters.js
│   ├── package.json
│   ├── vite.config.js      # Build outputs to ../backend/static/
│   ├── tailwind.config.js
│   └── index.html
├── config/
│   ├── nginx.conf          # Nginx reverse proxy config
│   └── trans-script-web.service  # systemd unit file
├── docs/
│   ├── current-architecture.md
│   ├── research-agent-prompt.md
│   └── research-questions.md
├── deploy.sh               # Automated deployment script
├── README.md
└── .gitignore
```

## Development Setup

### Backend

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r backend/requirements.txt

# Run development server
python -m backend.app
# Server starts at http://localhost:5000
```

### Frontend

```bash
cd frontend
npm install
npm run dev
# Dev server at http://localhost:3000 (proxies /api and /socket.io to :5000)
```

### Production (via deploy.sh)

```bash
sudo ./deploy.sh
# Installs all deps, builds frontend, configures Nginx + systemd
```

The frontend builds to `backend/static/` (configured in `vite.config.js`). Flask serves the SPA from that directory.

### Production server

```bash
gunicorn --worker-class geventwebsocket.gunicorn.workers.GeventWebSocketWorker \
    --workers 1 --bind 127.0.0.1:5000 --timeout 300 backend.wsgi:app
```

Single worker required because WebSocket (gevent) does not support multiple workers.

## Environment Variables

| Variable | Purpose | Default |
|----------|---------|---------|
| `SECRET_KEY` | Flask session signing + admin key derivation | Random per restart |
| `DEEPL_API_KEY` | DeepL Free API key (optional) | Empty (disabled) |
| `MYMEMORY_EMAIL` | MyMemory registered email for higher quota | Empty |
| `CACHE_MEMORY_SIZE` | Max L1 cache entries | `10000` |
| `ADMIN_EMAILS` | Comma-separated admin emails (auto-promoted) | Empty |
| `SMTP_HOST` | SMTP server for OTP emails | `smtp.gmail.com` |
| `SMTP_PORT` | SMTP port | `587` |
| `SMTP_USER` | SMTP username | Empty |
| `SMTP_PASS` | SMTP password | Empty |
| `SMTP_FROM` | From address for emails | `Traducao <noreply@example.com>` |
| `ADMIN_SESSION_EXPIRY_HOURS` | Admin session lifetime | `4` |
| `SESSION_COOKIE_SECURE` | Secure cookie flag (set for HTTPS) | `false` |

Environment file in production: `/etc/trans-script-web/env`

## Key Conventions

### Code Style

- **Python**: No linter/formatter configured. Follow existing code patterns. No type annotations on most functions (some in `admin_auth.py` and `engine/`). Docstrings in Portuguese.
- **JavaScript/React**: Functional components with hooks. No TypeScript. JSX files use `.jsx` extension. Tailwind for styling.
- **No test suite**: There are no automated tests in this project.
- **No CI/CD pipeline**: Deployment is manual via `deploy.sh`.

### Commit Messages

- Written in Portuguese or English (mixed in history)
- Format: `type: description` (e.g., `feat:`, `fix:`, `Add`, `Fix:`, `Remove`)
- Keep commit messages concise

### Naming Conventions

- Python: `snake_case` for functions/variables, `PascalCase` for classes
- JavaScript: `camelCase` for functions/variables, `PascalCase` for components
- API routes: lowercase with hyphens (`/api/admin/reconcile-storage`)
- Job IDs: 8-character hex strings (validated by regex `^[a-f0-9]{8}$`)

### Security Considerations

- All API routes use `@login_required` or `@admin_required` decorators
- Job ID validation prevents path traversal (`_validate_job_id`)
- ZIP extraction validates against ZIP Slip attacks (`_safe_zip_extract`)
- Admin tokens use AES-256-GCM encryption + HMAC-SHA256 + IP binding
- Password hashing via werkzeug's scrypt
- Rate limiting on uploads (per IP) and admin login attempts
- Security headers on all responses (X-Content-Type-Options, X-Frame-Options, etc.)
- OTP codes expire after 15 minutes, max 3 attempts

### Important Constraints

- **No external HTTP libraries**: All providers use `urllib` from Python stdlib only
- **Single Gunicorn worker**: Required for WebSocket/gevent compatibility
- **SQLite threading**: All DB operations use `threading.Lock` (`_db_lock` / `_admin_lock`)
- **Translation state**: Jobs exist in memory (`_jobs` dict) during execution, persisted to SQLite on completion
- **Storage quota**: Default 500 MB per user, tracked in `users.storage_used_bytes`
- **Job expiration**: Jobs expire after 7 days (`JOB_EXPIRY_DAYS`), cleanup runs every 24h

## API Endpoints

### Authentication
- `POST /api/auth/register` — Register with email + password
- `POST /api/auth/login` — Login with email + password
- `POST /api/auth/request-otp` — Request OTP for password recovery
- `POST /api/auth/verify-otp` — Verify OTP code
- `POST /api/auth/logout` — Logout
- `GET /api/auth/me` — Get current user info

### Translation Jobs
- `POST /api/upload` — Upload file(s) for translation
- `GET /api/jobs` — List user's jobs
- `GET /api/jobs/<id>` — Get job status
- `GET /api/jobs/<id>/download` — Download translated ZIP
- `GET /api/jobs/<id>/download/voipnow` — Download VoipNow TAR
- `POST /api/jobs/<id>/cancel` — Cancel running job
- `DELETE /api/jobs/<id>` — Delete job

### User History & Quota
- `GET /api/history` — User's job history
- `DELETE /api/history/<id>` — Delete specific job files
- `DELETE /api/history` — Bulk delete job files
- `GET /api/quota` — User's storage quota
- `GET /api/activity` — User's activity log

### Admin (requires admin token via `Authorization: Bearer <token>`)
- `POST /api/admin/login` — Get admin token
- `POST /api/admin/logout` — Revoke admin session
- `GET /api/admin/me` — Admin session info
- `GET /api/admin/users` — List all users
- `POST /api/admin/users/<id>/toggle-admin` — Promote/demote admin
- `DELETE /api/admin/users/<id>` — Delete user account
- `GET /api/admin/stats` — System statistics
- `GET /api/admin/activity` — Global activity log
- `GET /api/admin/job-history` — All job history
- `POST /api/admin/reconcile-storage` — Recalculate storage from disk

### Engine & Cache
- `GET /api/engine/stats` — Translation engine metrics
- `POST /api/cache/clear-untranslated` — Remove failed cache entries

## Adding a New Translation Provider

1. Create `backend/engine/providers/my_provider.py`
2. Extend `TranslationProvider` from `backend.engine.base`
3. Implement `translate(text) -> Optional[str]` and `is_available() -> bool`
4. Optionally override `translate_batch()` for native batch support
5. Register in `backend/engine/__init__.py` → `get_engine()` function
6. Use only `urllib` for HTTP requests (no external dependencies)

## Runtime Directories (gitignored)

- `backend/uploads/` — Temporary upload storage
- `backend/jobs/` — Job working directories (`{job_id}/input/`, `{job_id}/output/`)
- `backend/static/` — Frontend build output (served by Flask)
- `backend/users.db` — SQLite database
- `frontend/node_modules/` — npm packages
