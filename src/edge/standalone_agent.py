#!/usr/bin/env python3
"""
Sidra Edge Agent - Lightweight standalone monitoring agent.
Collects system metrics and sends to Central Brain.
"""

import asyncio
import json
import os
import socket
import subprocess
import time
import sqlite3
import urllib.request
import urllib.error
from pathlib import Path

# Configuration
CENTRAL_URL = os.getenv('SIDRA_CENTRAL_URL', 'http://192.168.92.145:8200')
AGENT_ID = os.getenv('SIDRA_AGENT_ID', socket.gethostname())
COLLECT_INTERVAL = int(os.getenv('SIDRA_COLLECT_INTERVAL', '30'))
BUFFER_PATH = '/var/lib/sidra-agent/buffer.db'

class MetricBuffer:
    def __init__(self, path):
        self.path = path
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(path)
        self.conn.execute('''CREATE TABLE IF NOT EXISTS buffer
            (id INTEGER PRIMARY KEY, data TEXT, created_at REAL)''')
        self.conn.commit()

    def add(self, data):
        self.conn.execute('INSERT INTO buffer (data, created_at) VALUES (?, ?)',
                         (json.dumps(data), time.time()))
        self.conn.commit()

    def get_batch(self, limit=100):
        cur = self.conn.execute('SELECT id, data FROM buffer ORDER BY id LIMIT ?', (limit,))
        return [(row[0], json.loads(row[1])) for row in cur.fetchall()]

    def remove(self, ids):
        if ids:
            placeholders = ','.join(['?'] * len(ids))
            self.conn.execute('DELETE FROM buffer WHERE id IN (' + placeholders + ')', ids)
            self.conn.commit()

def get_cpu_usage():
    try:
        with open('/proc/stat') as f:
            line = f.readline()
        parts = line.split()[1:8]
        total = sum(int(p) for p in parts)
        idle = int(parts[3])
        return round((1 - idle / total) * 100, 2) if total > 0 else 0
    except:
        return 0

def get_memory_usage():
    try:
        with open('/proc/meminfo') as f:
            lines = {l.split(':')[0]: int(l.split()[1]) for l in f if ':' in l}
        total = lines.get('MemTotal', 1)
        available = lines.get('MemAvailable', lines.get('MemFree', 0))
        return round((1 - available / total) * 100, 2)
    except:
        return 0

def get_disk_usage():
    try:
        st = os.statvfs('/')
        total = st.f_blocks * st.f_frsize
        free = st.f_bavail * st.f_frsize
        return round((1 - free / total) * 100, 2) if total > 0 else 0
    except:
        return 0

def get_load_avg():
    try:
        return os.getloadavg()[0]
    except:
        return 0

def get_gpu_metrics():
    try:
        result = subprocess.run(
            ['nvidia-smi', '--query-gpu=index,name,temperature.gpu,utilization.gpu,memory.used,memory.total',
             '--format=csv,noheader,nounits'],
            capture_output=True, text=True, timeout=10
        )
        if result.returncode != 0:
            return []

        gpus = []
        for line in result.stdout.strip().split('\n'):
            if line:
                parts = [p.strip() for p in line.split(',')]
                if len(parts) >= 6:
                    gpus.append({
                        'index': int(parts[0]),
                        'name': parts[1],
                        'temp': float(parts[2]) if parts[2] != '[N/A]' else 0,
                        'util': float(parts[3]) if parts[3] != '[N/A]' else 0,
                        'mem_used': int(parts[4]),
                        'mem_total': int(parts[5]),
                    })
        return gpus
    except:
        return []

def get_docker_stats():
    try:
        result = subprocess.run(
            ['docker', 'ps', '-q'],
            capture_output=True, text=True, timeout=10
        )
        if result.returncode != 0:
            return {'running': 0, 'containers': []}

        containers = result.stdout.strip().split('\n')
        return {'running': len([c for c in containers if c]), 'containers': []}
    except:
        return {'running': 0, 'containers': []}

def get_failed_services():
    try:
        result = subprocess.run(
            ['systemctl', '--failed', '--no-legend', '--plain'],
            capture_output=True, text=True, timeout=10
        )
        if result.returncode != 0:
            return []
        return [l.split()[0] for l in result.stdout.strip().split('\n') if l]
    except:
        return []

def collect_metrics():
    timestamp = time.time()
    hostname = socket.gethostname()

    metrics = {
        'timestamp': timestamp,
        'host': hostname,
        'cpu_percent': get_cpu_usage(),
        'memory_percent': get_memory_usage(),
        'disk_percent': get_disk_usage(),
        'load_1m': get_load_avg(),
        'gpus': get_gpu_metrics(),
        'docker': get_docker_stats(),
        'failed_services': get_failed_services(),
    }

    # Generate alerts
    alerts = []
    if metrics['cpu_percent'] > 90:
        alerts.append({'severity': 'critical', 'metric': 'cpu', 'value': metrics['cpu_percent'],
                      'message': 'CPU at ' + str(metrics['cpu_percent']) + '%'})
    elif metrics['cpu_percent'] > 80:
        alerts.append({'severity': 'high', 'metric': 'cpu', 'value': metrics['cpu_percent'],
                      'message': 'CPU at ' + str(metrics['cpu_percent']) + '%'})

    if metrics['memory_percent'] > 90:
        alerts.append({'severity': 'critical', 'metric': 'memory', 'value': metrics['memory_percent'],
                      'message': 'Memory at ' + str(metrics['memory_percent']) + '%'})

    if metrics['disk_percent'] > 90:
        alerts.append({'severity': 'critical', 'metric': 'disk', 'value': metrics['disk_percent'],
                      'message': 'Disk at ' + str(metrics['disk_percent']) + '%'})

    for gpu in metrics['gpus']:
        if gpu['temp'] > 85:
            alerts.append({'severity': 'critical', 'metric': 'gpu_temp', 'value': gpu['temp'],
                          'message': 'GPU ' + str(gpu['index']) + ' temp at ' + str(gpu['temp']) + 'C'})

    for svc in metrics['failed_services']:
        alerts.append({'severity': 'high', 'metric': 'service', 'value': svc,
                      'message': 'Service ' + svc + ' failed'})

    return {'metrics': metrics, 'alerts': alerts}

def send_to_central(data, buffer):
    try:
        payload_data = {
            'timestamp': time.time(),
            'host': AGENT_ID,
            'metrics': [
                {'name': 'sidra_cpu_percent', 'value': data['metrics']['cpu_percent'],
                 'timestamp': data['metrics']['timestamp'], 'labels': {'host': AGENT_ID}},
                {'name': 'sidra_memory_percent', 'value': data['metrics']['memory_percent'],
                 'timestamp': data['metrics']['timestamp'], 'labels': {'host': AGENT_ID}},
                {'name': 'sidra_disk_percent', 'value': data['metrics']['disk_percent'],
                 'timestamp': data['metrics']['timestamp'], 'labels': {'host': AGENT_ID}},
                {'name': 'sidra_load_1m', 'value': data['metrics']['load_1m'],
                 'timestamp': data['metrics']['timestamp'], 'labels': {'host': AGENT_ID}},
            ],
            'alerts': [
                {**a, 'timestamp': time.time(), 'host': AGENT_ID}
                for a in data['alerts']
            ],
        }

        # Add GPU metrics
        for gpu in data['metrics']['gpus']:
            payload_data['metrics'].extend([
                {'name': 'sidra_gpu_temp', 'value': gpu['temp'],
                 'timestamp': data['metrics']['timestamp'],
                 'labels': {'host': AGENT_ID, 'gpu': str(gpu['index']), 'name': gpu['name']}},
                {'name': 'sidra_gpu_util', 'value': gpu['util'],
                 'timestamp': data['metrics']['timestamp'],
                 'labels': {'host': AGENT_ID, 'gpu': str(gpu['index']), 'name': gpu['name']}},
            ])

        payload = json.dumps(payload_data).encode('utf-8')

        req = urllib.request.Request(
            CENTRAL_URL + '/api/v1/ingest/batch',
            data=payload,
            headers={'Content-Type': 'application/json'},
            method='POST'
        )

        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status == 200
    except Exception as e:
        print('Send failed: ' + str(e))
        buffer.add(data)
        return False

def flush_buffer(buffer):
    items = buffer.get_batch(50)
    if not items:
        return

    sent_ids = []
    for item_id, data in items:
        try:
            if send_to_central(data, buffer):
                sent_ids.append(item_id)
        except:
            pass

    if sent_ids:
        buffer.remove(sent_ids)
        print('Flushed ' + str(len(sent_ids)) + ' buffered items')

def main():
    print('Sidra Edge Agent starting on ' + AGENT_ID)
    print('Central Brain: ' + CENTRAL_URL)
    print('Collect interval: ' + str(COLLECT_INTERVAL) + 's')

    buffer = MetricBuffer(BUFFER_PATH)

    while True:
        try:
            data = collect_metrics()

            # Log summary
            m = data['metrics']
            print('[' + time.strftime('%H:%M:%S') + '] CPU: ' + str(m['cpu_percent']) + '% | Mem: ' + str(m['memory_percent']) + '% | Disk: ' + str(m['disk_percent']) + '% | GPUs: ' + str(len(m['gpus'])) + ' | Alerts: ' + str(len(data['alerts'])))

            # Send to central
            send_to_central(data, buffer)

            # Try to flush buffer periodically
            flush_buffer(buffer)

        except Exception as e:
            print('Error: ' + str(e))

        time.sleep(COLLECT_INTERVAL)

if __name__ == '__main__':
    main()
