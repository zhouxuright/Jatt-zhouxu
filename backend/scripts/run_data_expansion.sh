#!/bin/bash
# ============================================================================
# Ultimate Data Expansion Runner
# ============================================================================
# This script copies the expansion script into the Docker container and
# executes it with live progress output.
#
# Usage:
#   chmod +x run_data_expansion.sh
#   ./run_data_expansion.sh [--status] [--phases 1,2,3,4,5]
# ============================================================================

set -e

CONTAINER_NAME="legalintelligentassistancesystem-backend-1"
SCRIPT_PATH="$(dirname "$0")/ultimate_data_expansion.py"
CONTAINER_SCRIPT="/app/scripts/ultimate_data_expansion.py"

# Colors
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

echo -e "${BLUE}========================================================================${NC}"
echo -e "${BLUE}  Ultimate Legal Data Expansion Runner${NC}"
echo -e "${BLUE}========================================================================${NC}"
echo ""

# Check if Docker container is running
if ! docker ps --format '{{.Names}}' | grep -q "^${CONTAINER_NAME}$"; then
    echo -e "${RED}ERROR: Container '${CONTAINER_NAME}' is not running.${NC}"
    echo "Available containers:"
    docker ps --format '  {{.Names}} ({{.Status}})'
    exit 1
fi

echo -e "${GREEN}[OK]${NC} Container '${CONTAINER_NAME}' is running"

# Check if script exists locally
if [ ! -f "$SCRIPT_PATH" ]; then
    echo -e "${RED}ERROR: Script not found at ${SCRIPT_PATH}${NC}"
    exit 1
fi

echo -e "${GREEN}[OK]${NC} Local script found"

# Copy script into container
echo -e "${YELLOW}[..]${NC} Copying script into container..."
docker cp "$SCRIPT_PATH" "${CONTAINER_NAME}:${CONTAINER_SCRIPT}"
echo -e "${GREEN}[OK]${NC} Script copied to ${CONTAINER_SCRIPT}"

# Create data directory if needed
docker exec "$CONTAINER_NAME" mkdir -p /app/data

# Parse arguments
STATUS_ONLY=""
EXTRA_ARGS=""

for arg in "$@"; do
    if [ "$arg" = "--status" ]; then
        STATUS_ONLY="yes"
    else
        EXTRA_ARGS="$EXTRA_ARGS $arg"
    fi
done

echo ""
echo -e "${BLUE}========================================================================${NC}"

if [ -n "$STATUS_ONLY" ]; then
    echo -e "${BLUE}  Checking database status...${NC}"
    echo -e "${BLUE}========================================================================${NC}"
    echo ""
    docker exec "$CONTAINER_NAME" python "$CONTAINER_SCRIPT" --status
    exit 0
fi

# Show current status before expansion
echo -e "${BLUE}  Current database status (before expansion):${NC}"
echo -e "${BLUE}========================================================================${NC}"
docker exec "$CONTAINER_NAME" python "$CONTAINER_SCRIPT" --status

echo ""
echo -e "${YELLOW}Starting expansion${EXTRA_ARGS:+ with phases: $EXTRA_ARGS}${NC}"
echo -e "${YELLOW}Press Ctrl+C to stop (checkpoint will be saved for resume)${NC}"
echo ""

# Run the expansion script with live output
# Use -T to disable pseudo-TTY allocation for better log streaming
docker exec "$CONTAINER_NAME" python "$CONTAINER_SCRIPT" $EXTRA_ARGS 2>&1 | while IFS= read -r line; do
    # Color-code output
    if echo "$line" | grep -q "PHASE [0-9]"; then
        echo -e "${GREEN}${line}${NC}"
    elif echo "$line" | grep -q "complete"; then
        echo -e "${GREEN}${line}${NC}"
    elif echo "$line" | grep -q "ERROR\|FAILED\|error"; then
        echo -e "${RED}${line}${NC}"
    elif echo "$line" | grep -q "progress\|Progress"; then
        echo -e "${YELLOW}${line}${NC}"
    else
        echo "$line"
    fi
done

EXIT_CODE=${PIPESTATUS[0]}

echo ""
echo -e "${BLUE}========================================================================${NC}"
if [ $EXIT_CODE -eq 0 ]; then
    echo -e "${GREEN}  Expansion completed successfully!${NC}"
else
    echo -e "${RED}  Expansion exited with code ${EXIT_CODE}${NC}"
    echo -e "${YELLOW}  You can re-run to resume from checkpoint.${NC}"
fi
echo -e "${BLUE}========================================================================${NC}"
echo ""

# Show final status
echo -e "${BLUE}Final database status:${NC}"
docker exec "$CONTAINER_NAME" python "$CONTAINER_SCRIPT" --status

exit $EXIT_CODE
