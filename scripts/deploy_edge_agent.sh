#!/bin/bash
# Deploy Sidra Edge Agent to a server
# Usage: ./deploy_edge_agent.sh <server_ip> [central_url]

set -e

SERVER_IP="${1:?Usage: $0 <server_ip> [central_url]}"
CENTRAL_URL="${2:-http://192.168.92.145:8200}"
SSH_USER="${SSH_USER:-sidra}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "=== Deploying Sidra Edge Agent to $SERVER_IP ==="

# Create agent directory
ssh -o StrictHostKeyChecking=no ${SSH_USER}@${SERVER_IP} "sudo mkdir -p /opt/sidra-edge-agent /var/lib/sidra-agent /var/log/sidra-agent && sudo chown -R ${SSH_USER}:${SSH_USER} /opt/sidra-edge-agent /var/lib/sidra-agent /var/log/sidra-agent"

# Copy the agent script
scp -o StrictHostKeyChecking=no ${SCRIPT_DIR}/../src/edge/standalone_agent.py ${SSH_USER}@${SERVER_IP}:/opt/sidra-edge-agent/agent.py

# Make executable
ssh -o StrictHostKeyChecking=no ${SSH_USER}@${SERVER_IP} "chmod +x /opt/sidra-edge-agent/agent.py"

# Create systemd service
ssh -o StrictHostKeyChecking=no ${SSH_USER}@${SERVER_IP} "sudo tee /etc/systemd/system/sidra-edge-agent.service > /dev/null << 'SERVICE_EOF'
[Unit]
Description=Sidra Edge Agent - Infrastructure Monitoring
After=network.target docker.service
Wants=docker.service

[Service]
Type=simple
ExecStart=/usr/bin/python3 /opt/sidra-edge-agent/agent.py
Restart=always
RestartSec=10
Environment=SIDRA_CENTRAL_URL=${CENTRAL_URL}
Environment=SIDRA_COLLECT_INTERVAL=30
WorkingDirectory=/opt/sidra-edge-agent

[Install]
WantedBy=multi-user.target
SERVICE_EOF"

# Enable and start the service
ssh -o StrictHostKeyChecking=no ${SSH_USER}@${SERVER_IP} "sudo systemctl daemon-reload && sudo systemctl enable sidra-edge-agent && sudo systemctl restart sidra-edge-agent"

# Wait a bit for service to start
sleep 3

# Check status
echo "=== Agent Status ==="
ssh -o StrictHostKeyChecking=no ${SSH_USER}@${SERVER_IP} "sudo systemctl status sidra-edge-agent --no-pager | head -15"

echo ""
echo "=== Deployment Complete ==="
echo "Agent deployed to ${SERVER_IP}"
echo "Central Brain: ${CENTRAL_URL}"
