#!/bin/bash

# Monitoring Daemon Script
# Runs continuous monitoring in the background

set -e

PIDFILE="/tmp/devops-agent-monitor.pid"
LOGFILE="./logs/monitor.log"

mkdir -p ./logs

start() {
    if [ -f "$PIDFILE" ] && kill -0 $(cat "$PIDFILE") 2>/dev/null; then
        echo "Monitoring already running (PID: $(cat $PIDFILE))"
        exit 1
    fi

    echo "Starting monitoring daemon..."

    # Run in background
    nohup da monitor >> "$LOGFILE" 2>&1 &
    echo $! > "$PIDFILE"

    echo "Monitoring started (PID: $(cat $PIDFILE))"
    echo "Logs: $LOGFILE"
}

stop() {
    if [ ! -f "$PIDFILE" ]; then
        echo "Monitoring not running"
        exit 1
    fi

    PID=$(cat "$PIDFILE")
    if kill -0 "$PID" 2>/dev/null; then
        echo "Stopping monitoring (PID: $PID)..."
        kill "$PID"
        rm -f "$PIDFILE"
        echo "Monitoring stopped"
    else
        echo "Process not found, cleaning up PID file"
        rm -f "$PIDFILE"
    fi
}

status() {
    if [ -f "$PIDFILE" ] && kill -0 $(cat "$PIDFILE") 2>/dev/null; then
        echo "Monitoring running (PID: $(cat $PIDFILE))"
        echo ""
        echo "Recent logs:"
        tail -20 "$LOGFILE" 2>/dev/null || echo "No logs yet"
    else
        echo "Monitoring not running"
    fi
}

case "$1" in
    start)
        start
        ;;
    stop)
        stop
        ;;
    restart)
        stop
        sleep 2
        start
        ;;
    status)
        status
        ;;
    *)
        echo "Usage: $0 {start|stop|restart|status}"
        exit 1
        ;;
esac
