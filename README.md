# LogSense — Autonomous Incident Response Co-pilot

> Built for the **Google Cloud Rapid Agent Hackathon** · Elastic Track

LogSense transforms log analysis from a reactive, manual chore into a proactive, autonomous workflow. Instead of writing Kibana queries at 3 AM, you talk to an agent that has already read every log your system ever produced — and alerts you before you even know something is wrong.

## Features

### 🚨 Feature 1 — Proactive Anomaly Watchdog
The agent runs continuously in the background. Every 60 seconds it compares the current 5-minute error-rate window against the rolling 60-minute baseline using direct Elasticsearch aggregations. When Gemini classifies a deviation as anomalous, it:
- Broadcasts a severity-graded alert (P1/P2/P3) to all connected clients via WebSocket
- Automatically kicks off an investigation and streams findings back — **before anyone asks**

### 💥 Feature 2 — Blast Radius Calculator
Click one button to translate technical errors into business impact:
- **Users affected** (cardinality aggregation on failed transaction user IDs)
- **Transactions failed** + **revenue lost** (sum aggregation on failed transaction amounts)
- **Projected revenue loss/hour** (extrapolated from incident duration)
- **Downstream cascade failures** (services affected beyond the root-cause service)

### 📄 Feature 3 — One-Click Post-Mortem Generator
After the investigation conversation, one click generates a complete, shareable post-mortem document — timeline, root cause, blast radius, action items — formatted in the style of Google/Stripe/Cloudflare public post-mortems. Download as Markdown, paste into Notion, done.

## Architecture

```
User ──▶ Streamlit UI ──▶ FastAPI Backend
                              │
                    ┌─────────┼──────────────┐
                    │         │              │
              ADK Agent  Watchdog       Blast Radius
              (Gemini)   (async loop)   (direct ES)
                    │         │
             Elastic MCP   Gemini
              (stdio)    (anomaly eval)
                    │
            Elasticsearch (Elastic Cloud)
```

- **Google Cloud ADK** — agent orchestration with Gemini 2.0 Flash
- **Elastic MCP Server** (`@elastic/mcp-server-elasticsearch`) — `list_indices`, `get_mappings`, `search` tools
- **Elasticsearch** on Elastic Cloud — stores logs, metrics, transactions, deployments
- **FastAPI** — REST + WebSocket backend
- **Streamlit** — chat + alert UI
- **Cloud Run** — deployment target

## Quick Start

### 1. Prerequisites
- Python 3.12+
- Node.js 20+ (for Elastic MCP server)
- An [Elastic Cloud](https://cloud.elastic.co) cluster (free 14-day trial)
- A Google AI Studio API key or Google Cloud project with Vertex AI enabled

### 2. Clone and configure
```bash
git clone https://github.com/YOUR_USERNAME/logsense.git
cd logsense
cp .env.example .env
# Edit .env with your ES_URL, ES_API_KEY, GOOGLE_API_KEY
```

### 3. Install dependencies
```bash
pip install -r backend/requirements.txt
pip install -r frontend/requirements.txt
```

### 4. Ingest synthetic data
```bash
cd backend
python data/generate.py
```
This creates ~4 indices with realistic e-commerce logs including a pre-baked incident (payment-service deployment at 14:51 UTC → connection pool exhaustion → 17-minute outage affecting ~2,800 users and ~$142K in transactions).

### 5. Run
```bash
# Terminal 1 — backend
cd backend
uvicorn main:app --reload --port 8000

# Terminal 2 — frontend
cd frontend
streamlit run app.py
```

Open http://localhost:8501

### 6. Docker (alternative)
```bash
cp .env.example .env  # fill in your keys
docker compose up --build
```

## Demo Script

1. Open the app — the Watchdog panel shows "monitoring…"
2. Click **"Why did checkout fail at 14:52?"** starter prompt
3. Watch the agent call `search` tools in real time and explain the incident
4. Ask **"Was there a deployment before the incident?"** — agent finds the 14:51 deployment
5. Click **"Calculate Impact"** in the Blast Radius panel
6. Click **"Generate Post-Mortem"** — download the resulting `.md` file

## Technologies Used
- Google Gemini 2.0 Flash (via Google ADK)
- Google Cloud ADK (Agent Development Kit)
- Elastic Cloud + Elasticsearch 8
- `@elastic/mcp-server-elasticsearch` MCP server
- FastAPI, Streamlit, Python 3.12

## License
MIT
