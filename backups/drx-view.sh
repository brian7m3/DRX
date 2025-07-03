#!/bin/bash

# DRX Status Viewer with Tmux
# Usage: sudo ./drx-view.sh main|web

# Text formatting
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
BOLD='\033[1m'
NC='\033[0m' # No Color

# Display header
echo -e "${BOLD}========================================${NC}"
echo -e "${BOLD}    DRX Status Screen Viewer (Tmux)     ${NC}"
echo -e "${BOLD}========================================${NC}"

# Check if running as root
if [ "$EUID" -ne 0 ]; then
    echo -e "${RED}Please run as root (use sudo).${NC}"
    echo -e "${YELLOW}Example: sudo $0 main${NC}"
    exit 1
fi

# Check for tmux
if ! command -v tmux &> /dev/null; then
    echo -e "${RED}tmux is not installed. Installing now...${NC}"
    apt-get update && apt-get install -y tmux
    if [ $? -ne 0 ]; then
        echo -e "${RED}Failed to install tmux. Please install manually:${NC}"
        echo "sudo apt-get install tmux"
        exit 1
    fi
fi

# Check arguments
SERVICE=$1
if [ -z "$SERVICE" ] || [[ "$SERVICE" != "main" && "$SERVICE" != "web" ]]; then
    echo -e "${RED}Error: Please specify which DRX service to view (main or web)${NC}"
    echo -e "Usage: sudo $0 main|web"
    exit 1
fi

# Check if service exists
if ! systemctl list-unit-files | grep -q drx_${SERVICE}.service; then
    echo -e "${RED}Error: Service drx_${SERVICE} not found.${NC}"
    exit 1
fi

# Stop the service
echo -e "${YELLOW}Stopping drx_${SERVICE} service...${NC}"
systemctl stop drx_${SERVICE}
sleep 1

# Generate a unique session name with timestamp
SESSION_NAME="drx_${SERVICE}_$(date +%s)"
SESSION_SCRIPT="/home/brian/DRX/drx_${SERVICE}.py"

# Display tmux controls
echo -e "${BLUE}${BOLD}TMUX CONTROLS:${NC}"
echo -e "  ${YELLOW}• Ctrl+B then D${NC} = Detach (leave DRX running but exit viewer)"
echo -e "  ${YELLOW}• Ctrl+B then [${NC} = Scroll mode (use arrows/PageUp to scroll)"
echo -e "  ${YELLOW}• Ctrl+B then ?${NC} = Show all tmux commands"
echo -e "  ${YELLOW}• Ctrl+C${NC} = Exit DRX completely"
echo ""
echo -e "${BOLD}The service will restart automatically when you exit.${NC}"
echo -e "${GREEN}Starting DRX ${SERVICE} status screen in 3 seconds...${NC}"
sleep 3

# Create a new tmux session
tmux new-session -d -s "$SESSION_NAME" "cd /home/brian/DRX && sudo python3 $SESSION_SCRIPT"

# Check if session was created successfully
if ! tmux list-sessions | grep -q "$SESSION_NAME"; then
    echo -e "${RED}Failed to create tmux session.${NC}"
    systemctl start drx_${SERVICE}
    exit 1
fi

# Attach to the tmux session
tmux attach-session -t "$SESSION_NAME"

# After detach or exit, kill the tmux session
if tmux list-sessions | grep -q "$SESSION_NAME"; then
    echo -e "${YELLOW}Closing tmux session...${NC}"
    tmux kill-session -t "$SESSION_NAME"
fi

# Restart the service
echo -e "${YELLOW}Restarting drx_${SERVICE} service...${NC}"
systemctl start drx_${SERVICE}

echo -e "${GREEN}${BOLD}DRX ${SERVICE} service has been restarted.${NC}"
echo -e "${BLUE}Current date & time: $(date '+%Y-%m-%d %H:%M:%S')${NC}"
