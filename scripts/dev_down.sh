#!/bin/zsh
# Stop anything the demo stack started.
pkill -f "examples/demo_idp.py"
pkill -f "examples/billing_mcp_server.py"
pkill -f "examples/crm_mcp_server.py"
pkill -f "mcp-gateway.*main.py"
pkill -f "^\S*Python main.py$"
echo "stopped"
