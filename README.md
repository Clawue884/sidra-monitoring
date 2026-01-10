# DevOps Agent

AI-powered DevOps agent for infrastructure discovery, monitoring, documentation, and automation. Uses local Ollama for intelligent analysis and recommendations.

## Features

- **Network Discovery**: Scan networks and discover hosts, open ports, services
- **Server Analysis**: Deep inspection of servers (CPU, memory, disk, processes)
- **Docker Discovery**: Discover containers, services, swarm configuration
- **Database Detection**: Find PostgreSQL, MySQL, MongoDB, Redis instances
- **Storage Analysis**: Discover GlusterFS, NFS, LVM configurations
- **AI-Powered Documentation**: Generate comprehensive infrastructure docs
- **Continuous Monitoring**: Real-time monitoring with alerting
- **Security Analysis**: Identify vulnerabilities and misconfigurations

## Quick Start

### Prerequisites

- Python 3.10+
- [Ollama](https://ollama.ai) running locally
- SSH access to target servers
- WireGuard VPN configured (for remote networks)

### Installation

```bash
cd devops-agent

# Create virtual environment
python -m venv venv
source venv/bin/activate

# Install dependencies
pip install -e .

# Configure
cp .env.example .env
# Edit .env with your settings
```

### Configuration

Edit `.env`:

```bash
# Ollama
OLLAMA_HOST=http://localhost:11434
OLLAMA_MODEL=llama3.2

# SSH Credentials
SSH_USER=root
SSH_PASSWORD=123456
SSH_ALT_USER=sidra
SSH_ALT_PASSWORD=Wsxk_8765

# Networks to scan (your VPN networks)
SCAN_NETWORKS=192.168.71.0/24,192.168.92.0/24,192.168.91.0/24
```

### Usage

#### Full Infrastructure Discovery

```bash
# Discover and analyze entire infrastructure
da discover

# Output to specific file
da discover --output infrastructure.json
```

#### Quick Host Scan

```bash
# Scan a single host
da scan 192.168.71.10

# Scan a network
da network 192.168.71.0/24

# Quick ping scan
da network 192.168.71.0/24 --quick
```

#### Generate Documentation

```bash
# Generate full documentation
da document --discover

# Generate from existing data
da document --input discovery_result.json --output docs.md

# Generate daily report
da report --type daily
```

#### Continuous Monitoring

```bash
# Monitor discovered hosts
da monitor --hosts 192.168.71.10,192.168.71.11 --interval 60

# Auto-discover and monitor
da monitor
```

#### Start API Server

```bash
# Start the API
da api --port 8200
```

## API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/health` | GET | Health check |
| `/discover` | GET | Start full discovery |
| `/discovery/status` | GET | Get discovery results |
| `/scan` | POST | Scan single host |
| `/network/scan` | POST | Scan network |
| `/monitor/start` | POST | Start monitoring |
| `/monitor/stop` | POST | Stop monitoring |
| `/monitor/status` | GET | Get monitoring status |
| `/monitor/alerts` | GET | Get active alerts |
| `/document` | POST | Generate documentation |
| `/document/daily` | GET | Get daily report |

### Example API Usage

```bash
# Scan a host
curl -X POST http://localhost:8200/scan \
  -H "Content-Type: application/json" \
  -d '{"host": "192.168.71.10"}'

# Start monitoring
curl -X POST http://localhost:8200/monitor/start \
  -H "Content-Type: application/json" \
  -d '{"hosts": ["192.168.71.10", "192.168.71.11"], "interval": 60}'

# Get alerts
curl http://localhost:8200/monitor/alerts
```

## Docker Deployment

```bash
# Start agent only (Ollama running separately)
docker-compose up -d devops-agent

# Start with bundled Ollama
docker-compose --profile with-ollama up -d

# Start full stack (with Redis and Grafana)
docker-compose --profile full up -d
```

## Discovery Capabilities

### Network Scanning
- Ping sweep for live hosts
- Port scanning (common ports + custom)
- Service detection
- SSH accessibility check

### Server Discovery
- OS and kernel info
- CPU, memory, disk usage
- Network interfaces
- Running processes
- Systemd services
- Installed packages
- User accounts
- Cron jobs

### Docker Discovery
- Docker version and info
- Swarm configuration
- Running containers
- Services and stacks
- Networks and volumes
- Container resource usage

### Database Discovery
- PostgreSQL: databases, connections, replication
- MySQL/MariaDB: databases, version
- MongoDB: databases, replica sets
- Redis: memory, connections, replication

### Storage Discovery
- Local disk usage
- GlusterFS volumes and peers
- NFS exports and mounts
- LVM volumes

## Monitoring Thresholds

Default thresholds (configurable):

| Metric | Warning | Critical |
|--------|---------|----------|
| CPU | 70% | 90% |
| Memory | 80% | 95% |
| Disk | 80% | 95% |

## Security Notes

- Credentials are stored in `.env` (never commit!)
- SSH connections use password or key-based auth
- API has no authentication by default (add in production)
- Consider using SSH keys instead of passwords

## Ollama Integration

The agent uses Ollama for:
- Infrastructure analysis and insights
- Security vulnerability detection
- Performance recommendations
- Documentation generation
- Architecture diagram creation

Recommended models:
- `llama3.2` - General purpose
- `codellama` - Code analysis
- `mistral` - Fast inference

## Project Structure

```
devops-agent/
├── src/
│   ├── __init__.py
│   ├── cli.py              # CLI interface
│   ├── config.py           # Configuration
│   ├── agents/
│   │   ├── infrastructure_agent.py
│   │   ├── documentation_agent.py
│   │   └── monitoring_agent.py
│   ├── discovery/
│   │   ├── network.py      # Network scanning
│   │   ├── server.py       # Server discovery
│   │   ├── docker.py       # Docker discovery
│   │   ├── database.py     # Database discovery
│   │   ├── storage.py      # Storage discovery
│   │   └── services.py     # Service discovery
│   ├── api/
│   │   └── main.py         # FastAPI app
│   └── utils/
│       ├── ssh.py          # SSH utilities
│       └── logger.py
├── configs/
│   └── servers.yml         # Server inventory
├── output/                 # Discovery results
├── Dockerfile
├── docker-compose.yml
└── pyproject.toml
```

## License

MIT
