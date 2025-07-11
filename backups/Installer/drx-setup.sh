#!/bin/bash

set -e

#############################
# Start of drx-preinstall.sh
#############################

echo "==== DRX Pre-Install Script ===="
echo "Current Date/Time (UTC): 2025-07-11 21:28:27"

# Require the script to be run as root (sudo)
if [[ $EUID -ne 0 ]]; then
  echo "ERROR: This script must be run with sudo or as root."
  echo "Please re-run: sudo $0"
  exit 1
fi

# --- Update and upgrade packages ---
echo "Updating system packages..."
apt-get update
apt-get upgrade -y

# --- Install required apt packages ---
APT_PACKAGES="sox tmux alsa-utils python3-pip python3-flask python3-serial python3-requests python3-lgpio python3-bs4"
echo "Installing required system packages: $APT_PACKAGES"
apt-get install -y $APT_PACKAGES

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
echo "- Copy 0000-Welcome.wav to DRX/sounds/ if present"
echo "- Copy drx-control to /usr/local/bin/ with 0755 permissions if present"
echo "- Copy drx_main.service and drx_web.service to /etc/systemd/system/ with 0644 permissions if present"
echo "- Set all service files to use /home/drx/DRX/ path"
echo "- Add necessary cron jobs to root's crontab for log rotation and weather updates"
echo "- Reload systemd, enable and start drx_main.service and drx_web.service if service files are present"
read -p "Proceed? (yes/no): " CONFIRM

if [[ "$CONFIRM" != "yes" ]]; then
    echo "Aborted."
    exit 1
fi

# Hard-code the DRX installation path
DRX_BASE="/home/drx/DRX"

# Directory structure as specified - Removed "Repeater Activity"
DIRS=(
  "scripts"
  "service_logs"
  "sounds/extra"
  "static"
  "wx"
  "logs"
)

# Create DRX root and subdirectories
mkdir -p "$DRX_BASE"
for dir in "${DIRS[@]}"; do
  mkdir -p "$DRX_BASE/$dir"
  echo "Created directory: $DRX_BASE/$dir"
done

# Set permissions to 0777 for all directories
chmod -R 0777 "$DRX_BASE"

# Get the script's directory as source directory
SOURCE_DIR="$(pwd)"
echo "Using source directory: $SOURCE_DIR"

# Copy main files to DRX root
echo "Copying main files to $DRX_BASE..."
# Use explicit file names from the directory listing
if [ -f "$SOURCE_DIR/config.ini" ]; then
  cp -v "$SOURCE_DIR/config.ini" "$DRX_BASE/"
  echo "Copied config.ini"
fi

if [ -f "$SOURCE_DIR/drx_main.py" ]; then
  cp -v "$SOURCE_DIR/drx_main.py" "$DRX_BASE/"
  echo "Copied drx_main.py"
fi

if [ -f "$SOURCE_DIR/drx_web.py" ]; then
  cp -v "$SOURCE_DIR/drx_web.py" "$DRX_BASE/"
  echo "Copied drx_web.py"
fi

if [ -f "$SOURCE_DIR/cos-active" ]; then
  cp -v "$SOURCE_DIR/cos-active" "$DRX_BASE/"
  echo "Copied cos-active"
fi

if [ -f "$SOURCE_DIR/cos-inactive" ]; then
  cp -v "$SOURCE_DIR/cos-inactive" "$DRX_BASE/"
  echo "Copied cos-inactive"
fi

# Copy 0000-Welcome.wav to sounds directory
if [ -f "$SOURCE_DIR/0000-Welcome.wav" ]; then
  cp -v "$SOURCE_DIR/0000-Welcome.wav" "$DRX_BASE/sounds/"
  echo "Copied 0000-Welcome.wav to sounds directory"
fi

# Copy any files from the scripts directory
if [ -d "$SOURCE_DIR/scripts" ]; then
  cp -rv "$SOURCE_DIR/scripts/"* "$DRX_BASE/scripts/" 2>/dev/null || :
  echo "Copied scripts directory contents"
fi

# Copy any files from the static directory
if [ -d "$SOURCE_DIR/static" ]; then
  cp -rv "$SOURCE_DIR/static/"* "$DRX_BASE/static/" 2>/dev/null || :
  echo "Copied static directory contents"
fi

# Copy any files from the wx directory
if [ -d "$SOURCE_DIR/wx" ]; then
  cp -rv "$SOURCE_DIR/wx/"* "$DRX_BASE/wx/" 2>/dev/null || :
  echo "Copied wx directory contents"
fi

# Copy drx-control to /usr/local/bin/ with 0755 permissions
if [ -f "$SOURCE_DIR/drx-control" ]; then
  cp -v "$SOURCE_DIR/drx-control" /usr/local/bin/
  chmod 0755 /usr/local/bin/drx-control
  echo "Copied drx-control to /usr/local/bin/"
fi

# Copy and patch service files
services_enabled=0
for svc in drx_main.service drx_web.service; do
  if [ -f "$SOURCE_DIR/$svc" ]; then
    # Only modify the WorkingDirectory line, not paths inside ExecStart
    sed -e "s|^WorkingDirectory=.*$|WorkingDirectory=/home/drx/DRX|g" \
        "$SOURCE_DIR/$svc" > "/tmp/$svc"
    cp -v "/tmp/$svc" /etc/systemd/system/"$svc"
    chmod 0644 /etc/systemd/system/"$svc"
    rm "/tmp/$svc"
    services_enabled=1
    echo "Copied and patched $svc"
  fi
done

# Add crontab entries for log rotation and weather updates to root's crontab
echo "Adding cron jobs to root's crontab for log rotation and weather updates..."
# Use a more reliable method for updating crontab - write directly to the file
cat > /tmp/root-crontab << EOF
# Added by DRX installer
0 12 * * * /usr/local/bin/drx-control rotate main
0 12 * * * /usr/local/bin/drx-control rotate web
*/15 * * * * python3 /home/drx/DRX/wx/wx_gen.py
59 * * * * python3 /home/drx/DRX/wx/wx_gen.py
EOF

# Install the new crontab
crontab /tmp/root-crontab
echo "Crontab entries added successfully."

# Remove temporary file
rm /tmp/root-crontab

# Ensure correct ownership
chown -R drx:drx "$DRX_BASE"
echo "Set ownership of $DRX_BASE to drx:drx"

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
echo "Directory structure:"
find "$DRX_BASE" -type d | sort

echo "Files copied:"
find "$DRX_BASE" -type f | sort

echo
echo "Thank you for installing DRX 2.0!  To control your system, "
echo "enter: sudo drx-control"
echo "Check sudo crontab -e to modify entries made by DRX installer."
echo
echo "A system reboot is required for all group changes and services to work properly."
read -p "Would you like to reboot now? (yes/no): " REBOOT_CONFIRM
if [[ "$REBOOT_CONFIRM" == "yes" ]]; then
    echo "Rebooting system..."
    reboot
else
    echo "Please reboot your system before using DRX. This is needed for DRX to work properly."
fi