# Sidra Infrastructure Monitor

AI-powered infrastructure monitoring system with LLM analysis, real-time dashboards, and multi-network support. Uses local Ollama (Devstral) for intelligent analysis and recommendations.

## Architecture

```
┌─────────────────────────────────────────────────────────────────────────┐
│                        Central Brain (server045)                         │
│                         192.168.92.145                                   │
├─────────────────────────────────────────────────────────────────────────┤
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  ┌─────────────┐ │
│  │VictoriaMetrics│  │  OpenObserve │  │   Grafana    │  │ Uptime Kuma │ │
│  │   :8428      │  │    :5080     │  │    :3000     │  │    :3001    │ │
│  │  (Metrics)   │  │   (Logs)     │  │ (Dashboards) │  │  (Status)   │ │
│  └──────────────┘  └──────────────┘  └──────────────┘  └─────────────┘ │
│                                                                          │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐                   │
│  │  Ingest API  │  │  Report API  │  │    Ollama    │                   │
│  │   :8200      │  │    :8201     │  │   :11434     │                   │
│  │ (Collector)  │  │(LLM Dashboard)│  │  (Devstral)  │                   │
│  └──────────────┘  └──────────────┘  └──────────────┘                   │
└─────────────────────────────────────────────────────────────────────────┘
                                  ▲
                                  │ HTTPS/Metrics
        ┌─────────────────────────┼─────────────────────────┐
        │                         │                         │
        ▼                         ▼                         ▼
┌───────────────┐        ┌───────────────┐        ┌───────────────┐
│  Edge Agent   │        │  Edge Agent   │        │  Edge Agent   │
│  (server004)  │        │  (server041)  │        │  (server043)  │
│   Compute     │        │   GPU: 4090   │        │   GPU: 5090   │
└───────────────┘        └───────────────┘        └───────────────┘
     Sidra-91                  Sidra-92                 Sidra-92
```

## Features

### 🖥️ Infrastructure Monitoring
- **Multi-Network Support**: Monitor 192.168.91.x, 192.168.92.x, and additional networks
- **GPU Monitoring**: NVIDIA GPU temp, utilization, memory (RTX 4090, 5070 Ti, 5090)
- **System Metrics**: CPU, Memory, Disk, Load Average, Network I/O
- **Service Monitoring**: Docker containers, systemd services

### 🤖 AI-Powered Analysis
- **LLM Summaries**: Devstral model generates real-time infrastructure reports
- **Issue Detection**: Automatic identification of critical issues
- **Recommendations**: AI-powered suggestions for optimization

### 📊 Dashboards
- **LLM Dashboard**: Single-pane view with AI analysis (`http://192.168.92.145:8201/api/v1/report/dashboard`)
- **Grafana**: Detailed metrics and historical graphs (`http://192.168.92.145:3000`)
- **Uptime Kuma**: Service availability monitoring (`http://192.168.92.145:3001`)

### 🔔 Alerting
- Critical/High/Medium severity levels
- Real-time alert streaming
- Webhook, Email, SMS support (via Alertmanager)

## Quick Start

### 1. Deploy Central Brain (server045)

```bash
cd docker/central-brain
docker-compose up -d
```

### 2. Deploy Edge Agents

```bash
# Deploy to all servers
./scripts/deploy_edge_agent.sh 192.168.92.54   # server004
./scripts/deploy_edge_agent.sh 192.168.92.141  # server041 (GPU)
./scripts/deploy_edge_agent.sh 192.168.92.143  # server043 (GPU)
# ... repeat for all servers
```

### 3. Setup Ollama (on server045)

```bash
ollama pull devstral
```

### 4. Access Dashboards

| Service | URL | Credentials |
|---------|-----|-------------|
| **LLM Dashboard** | http://192.168.92.145:8201/api/v1/report/dashboard | None |
| **Grafana** | http://192.168.92.145:3000 | admin / SidraGrafana2024! |
| **Uptime Kuma** | http://192.168.92.145:3001 | Setup on first access |
| **OpenObserve** | http://192.168.92.145:5080 | admin@sidra.local / SidraMonitor2024! |
| **VictoriaMetrics** | http://192.168.92.145:8428 | None |

## Network Configuration

### Sidra-91 (Secondary Network)
- 192.168.91.62 - server012 (compute)
- 192.168.91.63 - server013 (compute)
- 192.168.91.64 - server014 (compute)
- 192.168.91.91 - server031 (compute)
- 192.168.91.92 - server032 (compute)

### Sidra-92 (Primary Network)
- 192.168.92.54 - server004 (compute)
- 192.168.92.58 - server008 (compute)
- 192.168.92.59 - server009 (compute)
- 192.168.92.81 - server021 (GPU: RTX 5070 Ti)
- 192.168.92.133 - server033 (compute)
- 192.168.92.134 - server034 (compute)
- 192.168.92.141 - server041 (GPU: RTX 4090)
- 192.168.92.143 - server043 (GPU: RTX 5090)
- 192.168.92.144 - server044 (compute)
- 192.168.92.145 - server045 (Central Brain)

## API Endpoints

### Report API (Port 8201)

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/health` | GET | Health check |
| `/api/v1/report/dashboard` | GET | HTML dashboard with AI analysis |
| `/api/v1/report/summary` | GET | Full JSON report with LLM analysis |
| `/api/v1/report/quick` | GET | Quick text summary |
| `/api/v1/report/network/{network}` | GET | Network-specific report |
| `/api/v1/networks` | GET | Network configuration |

#### Query Parameters (Dashboard)
- `network` - Filter by network (e.g., `192.168.92`)
- `role` - Filter by role (`gpu`, `compute`, `central`)
- `severity` - Filter alerts (`critical`, `high`, `medium`)
- `refresh` - Auto-refresh interval in seconds (default: 30)

### Ingest API (Port 8200)

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/health` | GET | Health check |
| `/api/v1/ingest` | POST | Receive metrics from edge agents |
| `/api/v1/alerts/recent` | GET | Get recent alerts |

## Edge Agent

The edge agent runs on each monitored server and collects:

- CPU usage (psutil)
- Memory usage
- Disk usage (root partition)
- Load average (1m, 5m, 15m)
- Network I/O (bytes sent/received)
- GPU metrics (nvidia-smi)
- Docker container status
- systemd service failures

### Manual Installation

```bash
# On target server
sudo mkdir -p /opt/sidra-edge-agent
sudo pip3 install psutil requests

# Copy agent
scp src/edge/standalone_agent.py user@server:/opt/sidra-edge-agent/agent.py

# Create systemd service
sudo cat > /etc/systemd/system/sidra-edge-agent.service << EOF
[Unit]
Description=Sidra Edge Agent
After=network.target

[Service]
Type=simple
ExecStart=/usr/bin/python3 /opt/sidra-edge-agent/agent.py
Restart=always
Environment=CENTRAL_BRAIN_URL=http://192.168.92.145:8200

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable --now sidra-edge-agent
```

## Stack Components

| Component | Purpose | Port |
|-----------|---------|------|
| **VictoriaMetrics** | Time-series database (Prometheus alternative) | 8428 |
| **OpenObserve** | Log aggregation (Loki alternative) | 5080 |
| **Grafana** | Dashboards and visualization | 3000 |
| **Uptime Kuma** | Simple uptime monitoring | 3001 |
| **Alertmanager** | Alert routing | 9093 |
| **Ingest API** | Metrics collection endpoint | 8200 |
| **Report API** | LLM-powered dashboard | 8201 |
| **Ollama** | Local LLM (Devstral) | 11434 |

## Monitoring Thresholds

| Metric | Warning | Critical |
|--------|---------|----------|
| CPU | 80% | 90% |
| Memory | 85% | 95% |
| Disk | 80% | 90% |
| GPU Temp | 75°C | 85°C |
| GPU Util | 90% | N/A |

## Development

### Project Structure

```
devops-agent/
├── src/
│   ├── central/
│   │   ├── ingest_api.py      # Metrics ingestion
│   │   └── report_api.py      # LLM dashboard
│   ├── edge/
│   │   └── standalone_agent.py # Edge agent
│   └── ...
├── docker/
│   └── central-brain/
│       ├── docker-compose.yml
│       ├── Dockerfile.ingest
│       ├── Dockerfile.report
│       └── grafana/
├── scripts/
│   └── deploy_edge_agent.sh
└── configs/
```

### Local Development

```bash
# Create virtual environment
python -m venv venv
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Run report API locally
cd src/central
python report_api.py
```

## Security Notes

- Default credentials should be changed in production
- Use environment variables for secrets
- Consider adding authentication to APIs
- Use HTTPS in production
- Restrict network access to monitoring ports

## License

MIT
