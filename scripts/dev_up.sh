#!/bin/zsh
# Start the full demo stack (idp :9300, billing :9101, crm :9102,
# gateway :8081 with OIDC inbound) as ONE supervised group.
# Run in a persistent terminal:  ./scripts/dev_up.sh
# Stop: Ctrl-C (kills the whole group), or ./scripts/dev_down.sh

cd "$(dirname "$0")/.."
PY=.venv/bin/python
LOG=/tmp/mcp-gw-demo
mkdir -p "$LOG"

export GATEWAY_JWKS_URL=http://127.0.0.1:9300/.well-known/jwks.json
export GATEWAY_JWT_ISS=http://127.0.0.1:9300

$PY examples/demo_idp.py           > "$LOG/idp.log" 2>&1 &
$PY examples/billing_mcp_server.py > "$LOG/billing.log" 2>&1 &
$PY examples/crm_mcp_server.py     > "$LOG/crm.log" 2>&1 &
sleep 1.5
$PY main.py                        > "$LOG/gateway.log" 2>&1 &

trap 'kill 0; echo "stack stopped"' INT TERM
echo "demo stack up: idp :9300, billing :9101, crm :9102, gateway :8081"
echo "logs: $LOG/*.log   —   Ctrl-C to stop everything"
wait
