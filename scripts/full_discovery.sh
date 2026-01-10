#!/bin/bash

# Full Infrastructure Discovery Script
# This script runs a complete infrastructure discovery and generates documentation

set -e

echo "==================================="
echo "  Sidra Infrastructure Discovery  "
echo "==================================="
echo ""

# Check if Ollama is running
echo "Checking Ollama connection..."
if ! curl -s http://localhost:11434/api/tags > /dev/null 2>&1; then
    echo "Warning: Ollama is not running. AI analysis will be limited."
    echo "Start Ollama with: ollama serve"
    echo ""
fi

# Create output directory
OUTPUT_DIR="./output/$(date +%Y%m%d_%H%M%S)"
mkdir -p "$OUTPUT_DIR"

echo "Output directory: $OUTPUT_DIR"
echo ""

# Run discovery
echo "Step 1: Running infrastructure discovery..."
da discover --output "$OUTPUT_DIR/discovery.json"

echo ""
echo "Step 2: Generating documentation..."
da document --input "$OUTPUT_DIR/discovery.json" --output "$OUTPUT_DIR/infrastructure_docs.md"

echo ""
echo "Step 3: Generating daily report..."
da report --type daily --output "$OUTPUT_DIR/daily_report.md"

echo ""
echo "==================================="
echo "  Discovery Complete!              "
echo "==================================="
echo ""
echo "Results saved to: $OUTPUT_DIR"
echo ""
echo "Files generated:"
ls -la "$OUTPUT_DIR"
