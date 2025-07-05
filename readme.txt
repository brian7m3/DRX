Copy all files to your DRX directory. 
Create needed directories under DRX directory.
Make sure everything is executable at the directory level.

*** Commands to help figure out USB to serial and Sound Device settings ***
lsusb command will show USB devices

aplay -l
aplay -D hw:2,0 /home/brian/DRX/sounds/1001.wav
aplay -D hw:2,0 --dump-hw-params /home/brian/DRX/sounds/1001.wav
****************************************************************************

*** Upgrade Pi OS ***
apt-get upgrade

*** Modules to Install ***
sudo apt-get update
sudo apt-get install sox
sudo apt-get install alsa-utils
apt-get install -y tmux
sudo apt-get install python3-pip

pip3 install flask
pip3 install pyserial
pip3 install lgpio (apt-get install python3-lgpio)

sudo usermod -aG gpio $USER
sudo usermod -aG audio $SUDO_USER

edit drx_main.py DRX_DIRECTORY = "/home/brian/DRX"  # Set this to your actual path
test drx_main.py and drx_web.py before adding them as a service to make sure they run ok in seperate sessions.
sudo python /home/brian/DRX/drx_main.py
sudo python /home/brian/DRX/drx-web.py

***** Install DRX as a service *****

1. Create the Service File
Edit drx_main.service to change any paths
sudo cp /home/brian/DRX/drx_main.service /etc/systemd/system/drx_main.service
sudo chmod +x /etc/systemd/system/drx_main.service

2. Ensure the Script Is Executable and in Place
ls -l /home/brian/DRX/drx_main.py
chmod +x /home/brian/DRX/drx_main.py

3. Reload systemd to Register the Service
sudo systemctl daemon-reload

4. Enable the Service to Start at Boot
sudo systemctl enable drx_main.service

5. Start the Service
sudo systemctl start drx_main.service

6. Check Status and Logs
sudo systemctl status drx_main.service
	See recent logs: sudo journalctl -u drx_main.service -n 50 --no-pager

7. Copy drx-control script to /usr/local/bin/drx-control and make executable
Edit drx-control first to correct any paths
sudo cp /home/brian/DRX/drx-control /usr/local/bin/drx-control
sudo chmod +x /usr/local/bin/drx-control

8. You need to do steps 1-6 with drx_web.py as well.

9. From now on, enter "drx-control" to work with drx at command prompt level.


*** Log Rotation ***
Service Log Rotation will occur any time that you run drx-control.
If you want if to happen even if you do not run drx-control, schedule when the rotate check occurs with cron.
sudo crontab -e
0 2 * * * /usr/local/bin/drx-control rotate main
0 2 * * * /usr/local/bin/drx-control rotate web

alsamixer
alsactl store
DRX automatically will load your stored settings.
