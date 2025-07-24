#!/bin/bash

set -e

#############################
# Start of drx-preinstall.sh
#############################

echo "==== DRX Pre-Install Script ===="
echo "Current Date/Time (UTC): $(date -u +"%Y-%m-%d %H:%M:%S")"

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
echo "- Copy config.ini, drx_main.py, drx_web.py, cos-active, cos-inactive, change_log.txt to the DRX root if present"
echo "- Copy all files and subdirectories from sounds/ and wx/ recursively"
echo "- Copy top-level files from scripts/ and static/ (not subfolders)"
echo "- Remove /home/drx/DRX/wx/wx_gen.py if present before copying"
echo "- Copy drx-control to /usr/local/bin/ with 0755 permissions if present"
echo "- Copy drx_main.service, drx_web.service, drx_wx.service to /etc/systemd/system/ with 0644 permissions if present, patching WorkingDirectory"
echo "- Comment out old DRX cron jobs in root's crontab for log rotation and weather updates"
echo "- Reload systemd, enable and start drx_main.service, drx_web.service, drx_wx.service if service files are present"
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

# --- MODIFICATION: Remove wx_gen.py if present before copying wx directory ---
if [ -f "$DRX_BASE/wx/wx_gen.py" ]; then
  rm -f "$DRX_BASE/wx/wx_gen.py"
  echo "Removed old $DRX_BASE/wx/wx_gen.py"
fi

# Copy main files to DRX root
echo "Copying main files to $DRX_BASE..."
for f in config.ini drx_main.py drx_web.py cos-active cos-inactive change_log.txt; do
  if [ -f "$SOURCE_DIR/$f" ]; then
    cp -v "$SOURCE_DIR/$f" "$DRX_BASE/"
    echo "Copied $f"
  fi
done

# Copy 0000-Welcome.wav to sounds directory (optional legacy behavior)
if [ -f "$SOURCE_DIR/0000-Welcome.wav" ]; then
  cp -v "$SOURCE_DIR/0000-Welcome.wav" "$DRX_BASE/sounds/"
  echo "Copied 0000-Welcome.wav to sounds directory"
fi

# Recursively copy provided sounds/ directory to DRX/sounds/
if [ -d "$SOURCE_DIR/sounds" ]; then
  rsync -av --delete "$SOURCE_DIR/sounds/" "$DRX_BASE/sounds/"
  echo "Copied all sounds directory contents recursively"
fi

# Copy only top-level files from the scripts directory
if [ -d "$SOURCE_DIR/scripts" ]; then
  find "$SOURCE_DIR/scripts" -maxdepth 1 -type f -exec cp -v {} "$DRX_BASE/scripts/" \;
  echo "Copied scripts directory top-level files"
fi

# Copy only top-level files from the static directory
if [ -d "$SOURCE_DIR/static" ]; then
  find "$SOURCE_DIR/static" -maxdepth 1 -type f -exec cp -v {} "$DRX_BASE/static/" \;
  echo "Copied static directory top-level files"
fi

# Recursively copy wx directory contents
if [ -d "$SOURCE_DIR/wx" ]; then
  rsync -av --delete "$SOURCE_DIR/wx/" "$DRX_BASE/wx/"
  echo "Copied wx directory contents recursively"
fi

# Copy drx-control to /usr/local/bin/ with 0755 permissions
if [ -f "$SOURCE_DIR/drx-control" ]; then
  cp -v "$SOURCE_DIR/drx-control" /usr/local/bin/
  chmod 0755 /usr/local/bin/drx-control
  echo "Copied drx-control to /usr/local/bin/"
fi

# Copy and patch service files (drx_main.service, drx_web.service, drx_wx.service)
services_enabled=0
for svc in drx_main.service drx_web.service drx_wx.service; do
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

# --- Comment out old DRX cron jobs in root's crontab (do not add new ones) ---
echo "Commenting out old DRX cron jobs in root's crontab (if present)..."
CRON_TEMP_OLD=$(mktemp)
CRON_TEMP_NEW=$(mktemp)
crontab -l 2>/dev/null > "$CRON_TEMP_OLD" || true

PATTERNS=(
    "/usr/local/bin/drx-control rotate main"
    "/usr/local/bin/drx-control rotate web"
    "wx_gen.py"
)
COMMENTED_LINES=()

cp "$CRON_TEMP_OLD" "$CRON_TEMP_NEW"
for pattern in "${PATTERNS[@]}"; do
    if grep -q "$pattern" "$CRON_TEMP_NEW"; then
        while IFS= read -r line; do
            if [[ "$line" == *"$pattern"* ]] && [[ "$line" != \#* ]]; then
                COMMENTED_LINES+=("$line")
                sed -i "s|^\(.*$pattern.*\)|# [DRX-REMOVED] \1|" "$CRON_TEMP_NEW"
            fi
        done < <(grep "$pattern" "$CRON_TEMP_OLD")
    fi
done

crontab "$CRON_TEMP_NEW"

if [ ${#COMMENTED_LINES[@]} -gt 0 ]; then
    echo
    echo "The following old DRX cron entries in root's crontab have been commented out as they are no longer necessary:"
    for line in "${COMMENTED_LINES[@]}"; do
        echo "  $line"
    done
    echo
    echo "These lines have been marked with '# [DRX-REMOVED]' in the crontab."
else
    echo "No old DRX cron entries were found in root's crontab."
fi

rm -f "$CRON_TEMP_OLD" "$CRON_TEMP_NEW"

# Ensure correct ownership
chown -R drx:drx "$DRX_BASE"
echo "Set ownership of $DRX_BASE to drx:drx"

# If any service files were copied, reload systemd and enable/start them
if [[ $services_enabled -eq 1 ]]; then
  echo "Reloading systemd daemon..."
  systemctl daemon-reload
  for svc in drx_main.service drx_web.service drx_wx.service; do
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
echo "Check sudo crontab -e to review or clean up any remaining entries."
echo
echo "A system reboot is required for all group changes and services to work properly."
read -p "Would you like to reboot now? (yes/no): " REBOOT_CONFIRM
if [[ "$REBOOT_CONFIRM" == "yes" ]]; then
    echo "Rebooting system..."
    reboot
else
    echo "Please reboot your system before using DRX. This is needed for DRX to work properly."
fi