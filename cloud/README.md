# GT Cloud Coding Agent

Internal cloud coding agent powered by the GT mini-SWE harness. Point it at a repo + task, watch it work in real-time, steer it mid-run, get a patch + receipt.

## Quickstart

### 1. Prerequisites

- Python 3.11+
- Node.js 18+
- Git
- A GitHub OAuth App ([create one](https://github.com/settings/developers))
  - Set callback URL to `http://localhost:8000/auth/callback`
- A model provider API key (DeepSeek, OpenRouter, etc.)

### 2. Install

```bash
git clone https://github.com/harneet2512/gt-harness.git
cd gt-harness
git checkout cloud/internal-harness

# Install Python dependencies
pip install -e ".[cloud,miniswe]"

# Install UI dependencies
cd cloud/ui
npm install
cd ../..
```

### 3. Configure

```bash
cp cloud/.env.example cloud/.env
# Edit cloud/.env — fill in GITHUB_CLIENT_ID, GITHUB_CLIENT_SECRET, JWT_SECRET, and provider key
```

### 4. Run

Terminal 1 — API server:
```bash
cd cloud
uvicorn cloud.server.app:app --port 8000 --reload
```

Terminal 2 — UI dev server:
```bash
cd cloud/ui
npm run dev
```

### 5. Use

1. Open http://localhost:5173
2. Log in with GitHub
3. Create a session: paste a GitHub repo URL + task description
4. Watch the agent work in real-time
5. Send steering messages to redirect the agent mid-run
6. View the patch + receipt when done

## Architecture

```
Browser (React) ←→ FastAPI ←→ mini-SWE DefaultAgent + GT engine
                      ↕
                   SQLite
```

- **Agent engine**: mini-SWE DefaultAgent with GT integration (indexing, localization, evidence delivery, receipts)
- **Steering**: messages queued and injected at agent step boundaries (between model calls)
- **Streaming**: Server-Sent Events (SSE) for real-time terminal feed
- **Auth**: GitHub OAuth (single-user/internal scope)
- **Storage**: SQLite for session state + event log

## API

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | /api/sessions | Create a coding session |
| GET | /api/sessions | List all sessions |
| GET | /api/sessions/:id | Session status |
| GET | /api/sessions/:id/events | SSE event stream |
| GET | /api/sessions/:id/result | Patch + receipt |
| POST | /api/sessions/:id/steer | Send steering message |
| POST | /api/sessions/:id/stop | Stop the agent |

## Known Limitations

- No Docker container isolation per session (agent runs in server process)
- Single-server deployment (no horizontal scaling)
- SQLite storage (no concurrent write scaling)
- GT features require the gt-index binary and groundtruth-mcp wheel
