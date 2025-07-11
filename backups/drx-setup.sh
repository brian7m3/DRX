#!/bin/bash

set -e

#############################
# Start of drx-preinstall.sh
#############################

echo "==== DRX Pre-Install Script ===="

# Require the script to be run as root (sudo)
if [[ $EUID -ne 0 ]]; then
  echo "ERROR: This script must be run with sudo or as root."
  echo "Please re-run: sudo $0"
  exit 1
fi

# Check if the user running sudo is 'drx'
if [ "$SUDO_USER" != "drx" ]; then
  echo "ERROR: This script must be run by user 'drx' with sudo."
  echo "Current user: $SUDO_USER"
  echo "Please login as 'drx' and run: sudo $0"
  exit 1
fi

# --- Update and upgrade packages ---
echo "Updating system packages..."
apt-get update
apt-get upgrade -y

# --- Install required apt packages ---
APT_PACKAGES="sox tmux python3-pip alsa-utils"
echo "Installing required system packages: $APT_PACKAGES"
apt-get install -y $APT_PACKAGES

# --- Python pip packages required ---
PIP_PACKAGES="flask pyserial lgpio requests"
echo "Installing required Python packages (system-wide)..."
pip3 install --upgrade $PIP_PACKAGES

# --- Add drx user to gpio and audio groups ---
if groups "drx" | grep -q '\bgpio\b'; then
    echo "User drx is already in the gpio group."
else
    echo "Adding drx to gpio group (you may need to log out/in for this to take effect)..."
    usermod -aG gpio drx
fi

if groups "drx" | grep -q '\baudio\b'; then
    echo "User drx is already in the audio group."
else
    echo "Adding drx to audio group (you may need to log out/in for this to take effect)..."
    usermod -aG audio drx
fi

echo "==== DRX Pre-Install Complete ===="
echo "If drx user was added to the gpio or audio group, please log out and log back in before running DRX."
echo

###########################
# End of drx-preinstall.sh
###########################

echo "This script will:"
echo "- Install DRX in the directory /home/drx/DRX/"
echo "- Create the necessary directory structure with permissions 0777"
echo "- Copy config.ini, drx_main.py, drx_web.py, drx-control.conf, and readme.txt to the DRX root if present"
echo "- Copy xpander.png to DRX/static/ if present"
echo "- Copy wx_config.ini and wx_gen.py to DRX/wx/ if present"
echo "- Copy 0000.wav to DRX/sounds/ if present"
echo "- Copy drx-control to /usr/local/bin/ with 0755 permissions if present"
echo "- Copy drx_main.service and drx_web.service to /etc/systemd/system/ with 0644 permissions if present"
echo "- Set all service files to use /home/drx/DRX/ path"
echo "- Reload systemd, enable and start drx_main.service and drx_web.service if service files are present"
read -p "Proceed? (yes/no): " CONFIRM

if [[ "$CONFIRM" != "yes" ]]; then
    echo "Aborted."
    exit 1
fi

# Hard-code the DRX installation path
DRX_BASE="/home/drx/DRX"

# Directory structure as specified
DIRS=(
  "Repeater Activity"
  "scripts"
  "service_logs"
  "sounds/extra"
  "static"
  "wx"
  "logs"  # Added logs directory
)

# Create DRX root and subdirectories
mkdir -p "$DRX_BASE"
for dir in "${DIRS[@]}"; do
  mkdir -p "$DRX_BASE/$dir"
done

# Set permissions to 0777 for all directories
chmod -R 0777 "$DRX_BASE"

# Files to optionally copy if present (to DRX root)
FILES=(
  "config.ini"
  "drx_main.py"
  "drx_web.py"
  "drx-control.conf"
  "readme.txt"
)

echo "Checking for files to copy to $DRX_BASE..."
for file in "${FILES[@]}"; do
  if [[ -f "$file" ]]; then
    echo "Copying $file to $DRX_BASE"
    cp -p "$file" "$DRX_BASE/"
  fi
done

# Copy xpander.png to DRX/static/ if present
if [[ -f "xpander.png" ]]; then
  echo "Copying xpander.png to $DRX_BASE/static/"
  cp -p "xpander.png" "$DRX_BASE/static/"
fi

# Copy wx files if present to DRX/wx/
for wxfile in wx_config.ini wx_gen.py; do
  if [[ -f "$wxfile" ]]; then
    echo "Copying $wxfile to $DRX_BASE/wx/"
    cp -p "$wxfile" "$DRX_BASE/wx/"
  fi
done

# Copy 0000.wav to DRX/sounds/ if present
if [[ -f "0000.wav" ]]; then
  echo "Copying 0000.wav to $DRX_BASE/sounds/"
  cp -p "0000.wav" "$DRX_BASE/sounds/"
fi

# Copy drx-control to /usr/local/bin/ with 0755 permissions if present
if [[ -f "drx-control" ]]; then
  echo "Copying drx-control to /usr/local/bin/"
  cp -p "drx-control" /usr/local/bin/
  chmod 0755 /usr/local/bin/drx-control
fi

# Copy and patch service files to /etc/systemd/system/ with 0644 permissions if present
services_enabled=0
for svc in drx_main.service drx_web.service; do
  if [[ -f "$svc" ]]; then
    # Set all service paths to /home/drx/DRX/
    sed "s|/home/.*/DRX/|/home/drx/DRX/|g" "$svc" > "/tmp/$svc"
    echo "Copying $svc to /etc/systemd/system/ with correct DRX path"
    cp -p "/tmp/$svc" /etc/systemd/system/"$svc"
    chmod 0644 /etc/systemd/system/"$svc"
    rm "/tmp/$svc"
    services_enabled=1
  fi
done

# Ensure correct ownership
chown -R drx:drx "$DRX_BASE"

# If any service files were copied, reload systemd and enable/start them
if [[ $services_enabled -eq 1 ]]; then
  echo "Reloading systemd daemon..."
  systemctl daemon-reload
  for svc in drx_main.service drx_web.service; do
    if [[ -f "/etc/systemd/system/$svc" ]]; then
      echo "Enabling $svc to start on boot..."
      systemctl enable "$svc"
      echo "Starting $svc now..."
      systemctl start "$svc"
    fi
  done
fi

echo "DRX directory structure and files setup complete."
find "$DRX_BASE" -type d

echo
echo "Thank you for installing DRX 2.0!  To control your system, enter: sudo drx-control"
echo
echo "A system reboot is required for all group changes and services to work properly."
read -p "Would you like to reboot now? (yes/no): " REBOOT_CONFIRM
if [[ "$REBOOT_CONFIRM" == "yes" ]]; then
    echo "Rebooting system..."
    reboot
else
    echo "Please reboot your system before using DRX. This is needed for DRX to work properly."
fi