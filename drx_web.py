import os
import json
import time
import configparser
import requests
from flask import Flask, render_template_string, redirect, url_for, request, session, send_from_directory, jsonify, flash

DRX_START_TIME = time.time()

# --- Load config for credentials and port ---
script_dir = os.path.dirname(os.path.realpath(__file__))
config_file_path = os.path.join(script_dir, 'config.ini')
config = configparser.ConfigParser()
config.read(config_file_path)

WEB_PORT = int(config.get("Web", "port", fallback="8080"))
WEB_USER = config.get("WebAuth", "username", fallback="admin")
WEB_PASS = config.get("WebAuth", "password", fallback="drxpass")
DTMF_LOG_FILE = os.path.join(script_dir, "dtmf.log")

STATE_FILE = os.path.join(script_dir, 'drx_state.json')  # NOTE: No longer used - kept for debug endpoint only
WEBCMD_FILE = '/tmp/drx_webcmd.json'
SOUND_DIRECTORY = config['Sound']['directory']
SOUND_FILE_EXTENSION = config['Sound']['extension']
LOG_FILE = '/tmp/drx_webconsole.log'

# --- State API Configuration ---
DRX_MAIN_API_URL = "http://127.0.0.1:5000/api/state"
HTTP_TIMEOUT = 2.0  # Timeout for HTTP requests to drx_main.py

app = Flask(__name__)
app.secret_key = "change_this_to_a_random_secret"

DASHBOARD_TEMPLATE = '''
<!doctype html>
<html>
<head>
<title>DRX Dashboard</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css?family=Roboto:400,700&display=swap" rel="stylesheet">
<style>
:root {
  --primary: #3949ab;
  --primary-light: #e3e6f0;
  --accent: #1976d2;
  --success: #388e3c;
  --warning: #fbc02d;
  --danger: #d32f2f;
  --background: #f5f7fa;
  --card: #fff;
  --grey: #bdbdbd;
}
body {
  background: var(--background);
  font-family: 'Roboto', Arial, sans-serif;
  margin: 0;
  padding: 0;
}
#main-card {
  background: var(--card);
  max-width: 950px;
  margin: 40px auto;
  border-radius: 18px;
  box-shadow: 0 8px 32px 0 rgba(31,38,135,0.14);
  padding: 2.5em 3em;
  position: relative;
}
h1, h2 {
  color: var(--primary);
  font-weight: 700;
  letter-spacing: 1px;
}
h1 {
  margin-top: 0;
  font-size: 2.1em;
}
h2 {
  border-bottom: 1px solid var(--primary-light);
  padding-bottom: 0.3em;
  margin-top: 2em;
  margin-bottom: 0.8em;
  font-size: 1.3em;
}
.card-section {
  background: var(--primary-light);
  border-radius: 14px;
  padding: 1.1em 1.5em 1.2em 1.5em;
  margin-bottom: 2.1em;
  box-shadow: 0 2px 8px 0 rgba(31,38,135,0.09);
}
form {
  margin-bottom: 1.3em;
}
input, select, textarea {
  font-size: 1.07em;
  border: 1.5px solid var(--grey);
  border-radius: 6px;
  padding: 0.45em;
  margin-right: 0.7em;
  background: #f9f9fc;
  color: #2d2d2d;
  transition: border 0.2s;
}
input:focus, select:focus, textarea:focus {
  outline: none;
  border-color: var(--primary);
}
button {
  font-size: 1.1em;
  border: none;
  border-radius: 6px;
  background: linear-gradient(90deg,var(--primary),var(--accent));
  color: #fff;
  padding: 0.53em 1.25em;
  cursor: pointer;
  transition: background 0.18s, box-shadow .18s;
  margin-top: 0.5em;
  margin-bottom: 0.5em;
  box-shadow: 0 1px 4px 0 rgba(31,38,135,0.07);
}
button:hover, button:focus {
  background: linear-gradient(90deg,var(--accent),var(--primary));
  box-shadow: 0 2px 8px 0 rgba(31,38,135,0.14);
}
#logout-btn { float: right; margin-top: 10px;}
.status-list li { font-size: 1.1em; margin-bottom: 0.7em;}
.status-good { color: var(--success); font-weight: bold; }
.status-warn { color: var(--warning); font-weight: bold; }
.status-bad { color: var(--danger); font-weight: bold; }
.logs, .serials {
  background: #212121; 
  color: #ececec; 
  font-family: 'Roboto Mono', monospace;
  font-size: 1em;
  padding: 1.1em; 
  border-radius: 9px; 
  margin-top: 0.7em; 
  margin-bottom: 1.2em; 
  overflow-x: auto; 
  max-height: 220px;
}
pre.stateblock {
  background: var(--primary-light);
  color: #222;
  padding: 1.1em;
  border-radius: 9px;
  font-family: 'Roboto Mono', monospace;
  font-size: 1em;
  margin: 0 0 1em 0;
  white-space: pre-line;
}
.label {
  background: var(--primary);
  color: #fff;
  border-radius: 4px;
  padding: 0.13em 0.56em;
  font-size: 1em;
  margin-right: 0.44em;
}
#message-timer {
  display: inline-block;
  font-size: 1.25em;
  font-weight: 700;
  background: #fff;
  border: 2px solid var(--primary);
  color: var(--primary);
  border-radius: 7px;
  padding: 0.1em 0.6em;
  min-width: 64px;
  text-align: center;
  margin-left: 0.6em;
  transition: color 0.2s, border 0.2s, background 0.2s;
}
#message-timer.ready {
  color: var(--success);
  border-color: var(--success);
  background: #e4fbe7;
}
#message-timer.running {
  color: var(--danger);
  border-color: var(--danger);
  background: #ffeaea;
}
/* Modal styling */
.modal {
  display: none; position: fixed; z-index: 999; left: 0; top: 0; width: 100vw; height: 100vh;
  overflow: auto; background-color: rgba(0,0,0,0.33);
}
.modal-content {
  background: #fefefe; margin: 7% auto; padding: 2em; border: 1px solid #888; width: 96%; max-width: 540px; border-radius: 14px; box-shadow: 0 4px 20px #3333;
  animation: popupIn 0.33s;
}
@keyframes popupIn {
  from { transform: scale(0.95); opacity: 0;}
  to   { transform: scale(1); opacity: 1;}
}
.close {
  color: var(--primary); float: right; font-size: 2em; font-weight: bold; cursor: pointer;
}
.close:hover { color: var(--danger); }
@media (max-width: 700px) {
  #main-card { padding: 1.1em 0.5em;}
  h1 { font-size: 1.4em;}
  h2 { font-size: 1.09em;}
  .card-section { padding: 0.7em 0.5em;}
  button { width: 100%; }
  #logout-btn { float: none; margin-top: 8px; }
  .modal-content { width: 99vw; padding: 1.2em; }
}
.bases-block {
  background: #e3e6f0;
}
.stateblock-1 { background: #cdd5df !important; }
.stateblock-2 { background: #cdd5df !important; }
.stateblock-3 { background: #cdd5df !important; }
.stateblock-4 { background: #cdd5df !important; }
.stateblock {
  color: #222;
  padding: 1em;
  border-radius: 8px;
  font-family: 'Roboto Mono', monospace;
  font-size: 1em;
  margin: 0 0 1em 0;
  white-space: pre-line;
  box-shadow: 0 1px 4px 0 rgba(31,38,135,0.03);
}
.modal {
  display: none; position: fixed; z-index: 999; left: 0; top: 0; width: 100vw; height: 100vh;
  overflow: auto; background-color: rgba(0,0,0,0.33);
}
.modal-content {
  background: #fefefe; margin: 7% auto; padding: 2em; border: 1px solid #888; width: 96%; max-width: 540px; border-radius: 14px; box-shadow: 0 4px 20px #3333;
  animation: popupIn 0.33s;
}
@keyframes popupIn {
  from { transform: scale(0.95); opacity: 0;}
  to   { transform: scale(1); opacity: 1;}
}
.close {
  color: #3949ab; float: right; font-size: 2em; font-weight: bold; cursor: pointer;
}
.close:hover { color: #d32f2f; }
.config-sections {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(300px, 1fr));
    gap: 20px;
    margin-bottom: 20px;
}
.config-section {
    background: var(--primary-light);
    border-radius: 12px;
    padding: 15px;
    box-shadow: 0 2px 8px rgba(0,0,0,0.05);
}
.config-section h3 {
    color: var(--primary);
    margin-top: 0;
    margin-bottom: 15px;
    font-size: 1.1em;
    border-bottom: 1px solid rgba(0,0,0,0.1);
    padding-bottom: 8px;
}
.form-group {
    margin-bottom: 12px;
    display: flex;
    flex-direction: column;
}
.form-group label {
    margin-bottom: 5px;
    font-weight: 500;
    font-size: 0.95em;
}
.form-group input[type="text"], 
.form-group input[type="password"],
.form-group input[type="number"],
.form-group select {
    padding: 8px;
    border: 1px solid #ccc;
    border-radius: 4px;
    width: 100%;
    box-sizing: border-box;
}
.form-group.checkbox {
    flex-direction: row;
    align-items: center;
}
.form-group.checkbox input[type="checkbox"] {
    margin-right: 8px;
}
.form-group.checkbox label {
    margin-bottom: 0;
}
@media (max-width: 700px) {
    .config-sections {
        grid-template-columns: 1fr;
    }
}
/* Config form styling */
.config-sections {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(300px, 1fr));
    gap: 20px;
    margin-bottom: 20px;
}
.help-text {
    font-size: 0.85em;
    color: #666;
    margin-top: 3px;
    font-style: italic;
}
.button-row {
    display: flex;
    flex-direction: row;
    gap: 10px;
    justify-content: flex-start;
    align-items: center;
}
.button-row form {
    margin: 0;
}
@media (max-width: 700px) {
    .button-row {
        flex-direction: column;
        align-items: stretch;
    }
    .button-row form {
        width: 100%;
    }
}
.logs {
  overflow-y: auto;
  max-height: 220px;
}
</style>
<script>
let updateStatus;
document.addEventListener("DOMContentLoaded", function() {
    // Play Selected (dropdown) form
    const dropdownForm = document.getElementById('play-dropdown-form');
    if (dropdownForm) {
        dropdownForm.addEventListener('submit', function(event) {
            event.preventDefault();
            const formData = new FormData(dropdownForm);
            fetch(dropdownForm.action, {
                method: 'POST',
                body: formData,
                credentials: 'same-origin',
                headers: {'X-Requested-With': 'XMLHttpRequest'}
            })
            .then(response => response.json())
            .then(data => {
                if (data.local_url) {
                    window.location = data.local_url;
                }
                updateStatus();
                updateSerialSection();
                updateLogsSection();
                updateStateSection();
            });
        });
    }
    // Play Input form
    const inputForm = document.getElementById('play-input-form');
    if (inputForm) {
        inputForm.addEventListener('submit', function(event) {
            event.preventDefault();
            const formData = new FormData(inputForm);
            fetch(inputForm.action, {
                method: 'POST',
                body: formData,
                credentials: 'same-origin',
                headers: {'X-Requested-With': 'XMLHttpRequest'}
            })
            .then(response => response.json())
            .then(data => {
                if (data.local_url) {
                    window.location = data.local_url;
                }
                updateStatus();
                updateSerialSection();
                updateLogsSection();
                updateStateSection();
            });
        });
    }

    function updateMessageTimer() {
        fetch("/api/message_timer", {credentials: 'same-origin'})
        .then(response => response.json())
        .then(data => {
            const el = document.getElementById('message-timer');
            if (!el) return;
            if (data.seconds_left > 0) {
                const mins = Math.floor(data.seconds_left / 60);
                const secs = data.seconds_left % 60;
                if (mins > 0) {
                    el.textContent = `${mins}m ${secs.toString().padStart(2, '0')}s`;
                } else {
                    el.textContent = `${secs}s`;
                }
                el.classList.add("running");
                el.classList.remove("ready");
            } else {
                el.textContent = "Ready";
                el.classList.add("ready");
                el.classList.remove("running");
            }
        });
    }
    setInterval(updateMessageTimer, 1000);
    updateMessageTimer();

    // Poll status every second
    updateStatus = function() {
        fetch("{{ url_for('status_api') }}", {credentials: 'same-origin'})
        .then(response => response.json())
        .then(data => {
            document.querySelectorAll('.status-currently-playing').forEach(function(el) {
                el.textContent = data.currently_playing || "None";
            });
            document.querySelectorAll('.status-last-played').forEach(function(el) {
                el.textContent = data.last_played || "None";
            });
            if (document.getElementById('playback-status')) {
                let statusLabel = data.playback_status || "Idle";
                if (statusLabel === "Restarting") {
                    statusLabel = "Pending Restart";
                }
                document.getElementById('playback-status').textContent = statusLabel;
            }
            if (document.getElementById('cos-state')) {
                document.getElementById('cos-state').textContent = data.cos_state ? "YES" : "NO";
                document.getElementById('cos-state').className = data.cos_state ? "status-good" : "status-warn";
            }
            if (document.getElementById('remote-device-state')) {
                let remote = data.remote_device_active ? "YES" : "NO";
                let remoteClass = data.remote_device_active ? "status-good" : "status-warn";
                let el = document.getElementById('remote-device-state');
                el.textContent = remote;
                el.className = remoteClass;
            }
        });
    };

    function updateSerialSection() {
        fetch("{{ url_for('api_serial_commands') }}", {credentials: 'same-origin'})
        .then(response => response.text())
        .then(html => {
            document.getElementById('serial-section').innerHTML = html;
        });
    }

    function updateLogsSection() {
        fetch("{{ url_for('api_log_entries') }}", {credentials: 'same-origin'})
        .then(response => response.text())
        .then(html => {
            document.getElementById('logs-section').innerHTML = html;
        });
    }

    function updateStateSection() {
        fetch("{{ url_for('api_state_blocks') }}", {credentials: 'same-origin'})
        .then(response => response.text())
        .then(html => {
            document.getElementById('state-section').innerHTML = html;
        });
    }

function updateCosMinutes() {
    fetch("/api/cos_minutes", {credentials: 'same-origin'})
    .then(response => response.json())
    .then(data => {
        const el = document.getElementById('cos-today-minutes');
        if (el) el.textContent = data.cos_today_minutes;
    });
}

    setInterval(updateCosMinutes, 1000);
    updateCosMinutes();
    setInterval(updateStatus, 1000);
    setInterval(updateSerialSection, 1000);
    setInterval(updateLogsSection, 1000);
    setInterval(updateStateSection, 1000);

    updateStatus();
    updateSerialSection();
    updateLogsSection();
    updateStateSection();

    // Help Modal
    var helpBtn = document.getElementById('help-btn');
    var helpModal = document.getElementById('help-modal');
    var closeBtn = document.getElementById('close-help');
    if (helpBtn && helpModal && closeBtn) {
        helpBtn.onclick = function() { helpModal.style.display = "block"; };
        closeBtn.onclick = function() { helpModal.style.display = "none"; };
        window.onclick = function(event) {
            if (event.target == helpModal) { helpModal.style.display = "none"; }
        };
    }
});
function updateUptime() {
    fetch("{{ url_for('api_drx_uptime') }}", {credentials: 'same-origin'})
    .then(response => response.json())
    .then(data => {
        var up = document.getElementById('drx-uptime');
        if(up) {
            up.textContent = data.drx_uptime;
            if(data.not_running) {
                up.classList.add('flash-red');
            } else {
                up.classList.remove('flash-red');
            }
        }
    });
}
setInterval(updateUptime, 1000);
window.addEventListener('DOMContentLoaded', updateUptime);
</script>
<script>
document.addEventListener('DOMContentLoaded', function() {
    // Convert Python boolean strings to proper checkbox state
    const checkboxes = document.querySelectorAll('input[type="checkbox"]');
    checkboxes.forEach(function(checkbox) {
        if (checkbox.getAttribute('data-value') === 'True') {
            checkbox.checked = true;
        }
    });
});
</script>
</head>
<body>
<div id="main-card">
    <form method="POST" action="{{ url_for('logout') }}" id="logout-btn">
        <button type="submit">Logout</button>
    </form>
    <h1>DRX Status Dashboard</h1>
    <b>DRX Uptime:</b> <span id="drx-uptime">{{ drx_uptime }}</span>
<div class="your-card">
    <b>Minutes of Activity:</b> <span id="cos-today-minutes" class="status-good" style="font-size:1.15em;">
                {{ state.get('cos_today_minutes', 0) }}
</div>
    <div class="card-section">
        <h2 style="display:inline;">Play Specific Track</h2>
        <button id="help-btn" type="button" style="float:right;margin-left:1em;">Help</button>
        <form method="POST" action="{{ url_for('play_track') }}" id="play-dropdown-form">
            <label>Track (dropdown):</label>
            <select name="track_dropdown">
                <option value="">--Select--</option>
                {% for file in all_files %}
                    <option value="{{ file }}">{{ file }}</option>
                {% endfor %}
            </select>
            <label>Play Method:</label>
            <select name="play_method">
              <option value="normal" selected>Normal (DRX)</option>
              <option value="local">Local (Web Page)</option>
            </select>
            <button type="submit">Play Selected</button>
        </form>
        <form method="POST" action="{{ url_for('play_track') }}" id="play-input-form">
            <label>Track (input):</label>
            <input name="track_input" type="text" placeholder="Input Serial Data if DRX" size="20">
            <label>Play Method:</label>
            <select name="play_method">
              <option value="normal" selected>Normal (DRX)</option>
              <option value="local">Local (Web Page)</option>
            </select>
            <button type="submit">Play Input</button>
        </form>
        <br>
        <b>Currently Playing:</b>
        <span class="status-good status-currently-playing">{{ currently_playing or "None" }}</span>
        <form method="POST" action="{{ url_for('stop_playback') }}" style="display:inline; margin-left:1em;">
            <button type="submit" style="font-size:0.95em;padding:0.25em 0.8em;">Stop Playback</button>
        </form>
        <br>
        <b>Message Timer</b><br>
        <span id="message-timer" class="ready">Ready</span><br>
        <b>Last Played:</b>
        <span class="status-good status-last-played">{{ last_played or "None" }}</span>
        <br>
        <b>Status:</b> <span id="playback-status">{{ playback_status or "Idle" }}</span>
        <br>
        <b>COS Active:</b>
        <span id="cos-state" class="{% if cos_state == 'YES' %}status-good{% else %}status-warn{% endif %}">{{ cos_state }}</span>
        <br>
        <b>Remote Device Active:</b>
        <span id="remote-device-state" class="status-warn">NO</span>
        <br>
        <b>Serial Port:</b>
        {% if not serial_port_missing %}
            <span class="status-good">OK</span>
        {% else %}
            <span class="status-bad">Missing</span>
        {% endif %}
        <br>
        <b>Sound Card:</b>
        {% if not sound_card_missing %}
            <span class="status-good">OK</span>
        {% else %}
            <span class="status-bad">Missing</span>
        {% endif %}
        <br>
        <!-- Weather System Label -->
        <b>Weather System:</b>
        <span class="{{ weather_class }}" style="color: {{ weather_color }};">{{ weather_status }}</span>
        <br>
        <!-- End Weather System Label -->
        <!-- Help Modal HTML -->
        <div id="help-modal" class="modal">
          <div class="modal-content">
            <span class="close" id="close-help">&times;</span>
            <h2>Help: DRX (Digital Repeater Xpander</h2>
            <ul>
              <li><b>Play Specific Track:</b> Use the dropdown to select a track or use the input to specify one directly.</li>
              <li><b>Play Method:</b> 
                <ul>
                  <li><b>Normal (DRX):</b> Sends the play command to the DRX backend, which plays it on the repeater.</li>
                  <li><b>Local (Web Page):</b> Plays the audio in your web browser only (does not transmit on repeater). You must enter the full file name in input box (1001.wav).</li>
                </ul>
              </li>
              <li>If you input serial data, use the correct format as expected by your DRX system.  Web commands do not need the &#92;n as shown below.</li>
              <li>You can use either method to play tracks, but only one at a time.</li>
                <ul>
              <h3>Play Functions </h3>
              All tracks must start with a P and end with &#92;n or in decimal 010.  Do not confuse this with a slash and the letter n. &#92;n means newline.
              <br>
              <br>
                  <li><b>I:</b> Interrupts the wav - P1001I.</li>
                  <li><b>R:</b> Repeats the wav - P1001R.  Repeats x number of times until plays through. *</li>
                  <li><b>P:</b> Pauses the wav - P1001P.  Pauses x number of times before giving up. *</li>
                  <li><b>W:</b> Waits for COS (including debounce) to go inactive before playing the wav - P1001W. *</li>
                  <li><b>M:</b> Message that plays when timer expires P1001M, P1001RM, P2000A5000M.  For example, a tail message that won't play more than x minutes. *</li>
                  <li><b>A:</b> Alternates between Bases and/or Single Tracks.  P4000A5000A6000I (example with Repeat suffix).  This can cross base types. ***</li>
                  <li><b>J:</b> Joins Bases and/or Single Tracks.  P5189RJ5300I (example with Repeat and Interrupt suffixes).  This can cross base types. ***</li>
                  <li><b>i:</b> Interrupts primary wav and immediately plays secondary even if COS active - P3050i3000.  The i command is not supported with the A or J suffix.</li>
              <h3>Record Functions</h3>
              All tracks must start with an R and end with &#92;n).
              <br>
              <br>
                  <li><b>Re</b> Brings up an "Echo Test" to record and playback user - Re9999 (where 9999 is the track to record).  The recorded track does not get overwritten until called again, so you can have multiple commands to store different tracks.  Track recording stops automatically after 1 minute if COS doesn't become inactive prior.  If recording doesn't start within 5 seconds, Echo Test aborts (echo-to.wav). Required files are "echo-start.wav", "echo-to.wav", and optional "echo-end.wav".  Place them in the "sounds" directory.  Set COS debounce time in config for fluttering signals. *</li>
              <h3>Special Functions:</h3>
                  <li><b>S:</b> Scripts - Can call a 4 digit script number in the DRX/scripts folder -> S1001 &#92;n.</li>
                  <li><b>W1:</b> Weather Conditions, if cos was active in the last 10 seconds, jumps to W2 -> W1 &#92;n. **</li>
                  <li><b>W1F:</b> Weather Conditions Forced, same as W1 but doesn't have the cos rule -> W1F &#92;n. **</li>
                  <li><b>W2:</b> Temperature -> W2 &#92;n. **</li>
                  <li><b>A1:</b> Activity Announcement "A1 &#92;n" - When called, announces the repeater activity for yesterday.  Need announce.wav, minute.wav, minutes.wav, and number files (1.wav,100.wav, etc. **)
              <h3>Bases:</h3> 
                  A base type is called by sending P&lt;base #&gt;.  <br>Example: config.ini defines rotating base as base=4200,end=4210,interval=5, P4200, will play 4201.wav and cycle to 4202.wav after 5 minutes.  This will continue and loop back to 4201.
                  <br>
                  Enter in controller as P<base> &#92;n - P5300&#92;n
                  <br>
                  <li><b>Rotation</b> Rotations through the tracks in the base +1 to end tracks. Changes to next track at interval expiration. *</li>
                  <li><b>Random</b> Randomly plays the tracks in the base +1 to end tracks. Changes to next track at interval expiration. *</li>
                  <li><b>SudoRandom</b> Pseudo random - same as random except plays all tracks in a base group before playing again.  Won't play same track in 2 cycles. *</li>
                  <br>
                  * Defined in Configuration Settings.
                  <br>
                  ** Waits for cos inactive with debounce.</li>
                  <br>
                  *** When using A and J suffixes, do not use suffixes on bases (P, R, etc.)  You can use I but if used on the first wav, the entire J sequence will stop on interrupt.  Suffixes are ok to use on single tracks with A or J.  A and J can not be used in the same command.
                </ul>
              </li>
          </div>
        </div>
    </div>
    <div class="card-section">
        <h2>Recent Log Entries</h2>
        <div class="logs" id="logs-section">
        {% for entry in web_log %}
            <div>{{ entry }}</div>
        {% endfor %}
        </div>
    </div>
    <div class="card-section">
        <h2>Recent Serial Commands</h2>
        <div class="serials" id="serial-section">
        {% for cmd in serial_history[:5] %}
            <div>{{ cmd }}</div>
        {% endfor %}
        </div>
    </div>
    <div class="card-section">
    <h2>Recent DTMF Entries</h2>
    <div class="logs" id="dtmf-log-section">
        Loading...
    </div>
    <a href="{{ url_for('download_dtmf_log') }}">Download Full DTMF Log</a>
    </div>
    <!-- === State section now comes after log section === -->
    <div class="card-section" id="state-section">
        {{ state_blocks_html|safe }}
    </div>
    <div class="card-section">
        <div class="button-row">
            <form method="POST" action="{{ url_for('restart_script_web') }}">
                <button type="submit" onclick="return confirm('Restart the DRX?')">Restart DRX</button>
            </form>
            <form method="POST" action="{{ url_for('reboot_system_web') }}">
                <button type="submit" onclick="return confirm('Reboot the system?')">Reboot System</button>
            </form>
            <form method="POST" action="{{ url_for('reload_config_web') }}">
                <button type="submit">Reload Configuration File</button>
            </form>
        </div>
    </div>
<script>
function updateDtmfLog() {
    fetch("{{ url_for('api_dtmf_log') }}")
    .then(response => response.text())
    .then(html => {
        document.getElementById('dtmf-log-section').innerHTML = html;
    });
}
setInterval(updateDtmfLog, 2000);
window.addEventListener('DOMContentLoaded', updateDtmfLog);
</script>
<div class="card-section">
    <h2>Configuration Settings</h2>
    <form method="POST" action="{{ url_for('edit_config_structured') }}" id="config-form">
        <div class="config-sections">

            <!-- Base Sections -->
            <div class="config-section">
                <h3>Rotation Base</h3>
                <div class="form-group">
                    <label for="rotation_base">Base (comma-separated):</label>
                    <input type="text" id="rotation_base" name="rotation_base" value="{{ config.get('Rotation', 'base', fallback='5300') }}">
                    <small class="help-text">Multiple bases separated by commas (e.g., 5300,5400,5500)</small>
                </div>
                <div class="form-group">
                    <label for="rotation_end">End (comma-separated):</label>
                    <input type="text" id="rotation_end" name="rotation_end" value="{{ config.get('Rotation', 'end', fallback='5331') }}">
                    <small class="help-text">End values corresponding to each base (e.g., 5331,5431,5531)</small>
                </div>
                <div class="form-group">
                    <label for="rotation_interval">Interval (minutes, comma-separated):</label>
                    <input type="text" id="rotation_interval" name="rotation_interval" value="{{ config.get('Rotation', 'interval', fallback='0') }}">
                    <small class="help-text">Interval values for each base (e.g., 0,5,3)</small>
                </div>
            </div>
            <div class="config-section">
                <h3>Random Base</h3>
                <div class="form-group">
                    <label for="random_base">Base (comma-separated):</label>
                    <input type="text" id="random_base" name="random_base" value="{{ config.get('Random', 'base', fallback='5400') }}">
                    <small class="help-text">Multiple bases separated by commas</small>
                </div>
                <div class="form-group">
                    <label for="random_end">End (comma-separated):</label>
                    <input type="text" id="random_end" name="random_end" value="{{ config.get('Random', 'end', fallback='5432') }}">
                    <small class="help-text">End values corresponding to each base</small>
                </div>
                <div class="form-group">
                    <label for="random_interval">Interval (minutes, comma-separated):</label>
                    <input type="text" id="random_interval" name="random_interval" value="{{ config.get('Random', 'interval', fallback='0') }}">
                    <small class="help-text">Interval values for each base</small>
                </div>
            </div>
            <div class="config-section">
                <h3>SudoRandom Base</h3>
                <div class="form-group">
                    <label for="sudorandom_base">Base (comma-separated):</label>
                    <input type="text" id="sudorandom_base" name="sudorandom_base" value="{{ config.get('SudoRandom', 'base', fallback='5600') }}">
                    <small class="help-text">Multiple bases separated by commas</small>
                </div>
                <div class="form-group">
                    <label for="sudorandom_end">End (comma-separated):</label>
                    <input type="text" id="sudorandom_end" name="sudorandom_end" value="{{ config.get('SudoRandom', 'end', fallback='5669') }}">
                    <small class="help-text">End values corresponding to each base</small>
                </div>
                <div class="form-group">
                    <label for="sudorandom_interval">Interval (minutes, comma-separated):</label>
                    <input type="text" id="sudorandom_interval" name="sudorandom_interval" value="{{ config.get('SudoRandom', 'interval', fallback='10') }}">
                    <small class="help-text">Interval values for each base</small>
                </div>
            </div>
            <!-- General Section -->
            <div class="config-section">
                <h3>General Settings</h3>
                <div class="form-group">
                    <label for="message_timer">Message Timer (minutes):</label>
                    <input type="number" id="message_timer" name="message_timer" min="0" max="120" value="{{ config.get('General', 'Message Timer', fallback='10') }}">
                    <small class="help-text">Time before message (P1234M) plays again.  Could be used so a tail message doesn't play every tail drop.</small>
                </div>
            </div>
            <!-- Serial Section -->
            <div class="config-section">
                <h3>Serial Settings</h3>
                <div class="form-group">
                    <label for="serial_port">Serial Port:</label>
                    <input type="text" id="serial_port" name="serial_port" value="{{ config.get('Serial', 'port', fallback='/dev/ttyUSB0') }}">
                </div>
                <div class="form-group">
                    <label for="serial_baudrate">Baud Rate:</label>
                    <select id="serial_baudrate" name="serial_baudrate">
                        {% for rate in [1200, 2400, 4800, 9600, 19200, 38400, 57600, 115200] %}
                            <option value="{{ rate }}" {% if config.get('Serial', 'baudrate', fallback='57600')|int == rate %}selected{% endif %}>{{ rate }}</option>
                        {% endfor %}
                    </select>
                </div>
                <div class="form-group">
                    <label for="serial_timeout">Timeout (seconds):</label>
                    <input type="number" id="serial_timeout" name="serial_timeout" min="0.1" max="10" step="0.1" value="{{ config.get('Serial', 'timeout', fallback='0.5') }}">
                    <small class="help-text">Time DRX waits for controller to send serial data.</small>
                </div>
                <div class="form-group">
                    <label for="serial_line_timeout">Line Timeout (seconds):</label>
                    <input type="number" id="serial_line_timeout" name="serial_line_timeout" min="0.1" max="10" step="0.1" value="{{ config.get('Serial', 'line_timeout', fallback='2.0') }}">
                    <small class="help-text">Time before clearing the serial buffer if 010 is not detected.</small>
                </div>
            </div>
            <!-- Sound Section -->
            <div class="config-section">
                <h3>Sound Settings</h3>
                <div class="form-group">
                    <label for="sound_directory">Sound Directory:</label>
                    <input type="text" id="sound_directory" name="sound_directory" value="{{ config.get('Sound', 'directory', fallback='/home/brian/DRX/sounds/') }}">
                </div>
                <div class="form-group">
                    <label for="sound_extension">File Extension:</label>
                    <select id="sound_extension" name="sound_extension">
                        <option value=".wav" {% if config.get('Sound', 'extension', fallback='.wav') == '.wav' %}selected{% endif %}>.wav</option>
                        <option value=".mp3" {% if config.get('Sound', 'extension', fallback='.wav') == '.mp3' %}selected{% endif %}>.mp3</option>
                        <option value=".ogg" {% if config.get('Sound', 'extension', fallback='.wav') == '.ogg' %}selected{% endif %}>.ogg</option>
                    </select>
                </div>
                <div class="form-group">
                    <label for="sound_device">Sound Device:</label>
                    <input type="text" id="sound_device" name="sound_device" value="{{ config.get('Sound', 'device', fallback='plughw:2,0') }}">
                </div>
            </div>
            <!-- GPIO Section -->
            <div class="config-section">
                <h3>GPIO Settings</h3>
                <div class="form-group">
                    <label for="cos_pin">COS Pin:</label>
                    <input type="number" id="cos_pin" name="cos_pin" min="0" value="{{ config.get('GPIO', 'cos_pin', fallback='16') }}">
                </div>
                <div class="form-group checkbox">
                    <input type="checkbox" id="cos_activate_level" name="cos_activate_level" {% if config.get('GPIO', 'cos_activate_level', fallback='False').lower() == 'true' %}checked{% endif %}>
                    <label for="cos_activate_level">COS Activate Level (High)</label>
                </div>
                <div class="form-group">
                    <label for="remote_busy_pin">Remote Busy Pin:</label>
                    <input type="number" id="remote_busy_pin" name="remote_busy_pin" min="0" value="{{ config.get('GPIO', 'remote_busy_pin', fallback='20') }}">
                </div>
                <div class="form-group checkbox">
                    <input type="checkbox" id="remote_busy_activate_level" name="remote_busy_activate_level" {% if config.get('GPIO', 'remote_busy_activate_level', fallback='False').lower() == 'true' %}checked{% endif %}>
                    <label for="remote_busy_activate_level">Remote Busy Activate Level (High)</label>
                </div>
                <div class="form-group">
                    <label for="cos_debounce_time">COS Debounce Time (seconds):</label>
                    <input type="number" id="cos_debounce_time" name="cos_debounce_time" min="0" step="0.1" value="{{ config.get('GPIO', 'cos_debounce_time', fallback='1.0') }}">
                </div>
                <div class="form-group">
                    <label for="max_cos_interruptions">Max COS Interruptions:</label>
                    <input type="number" id="max_cos_interruptions" name="max_cos_interruptions" min="0" value="{{ config.get('GPIO', 'max_cos_interruptions', fallback='3') }}">
                </div>
            </div>
            <!-- WebAuth Section -->
            <div class="config-section">
                <h3>Web Authentication</h3>
                <div class="form-group">
                    <label for="web_username">Username:</label>
                    <input type="text" id="web_username" name="web_username" value="{{ config.get('WebAuth', 'username', fallback='k1sox') }}">
                </div>
                <div class="form-group">
                    <label for="web_password">Password:</label>
                    <input type="password" id="web_password" name="web_password" value="{{ config.get('WebAuth', 'password', fallback='') }}" placeholder="Enter new password or leave blank to keep current">
                </div>
            </div>
            <!-- Web Section -->
            <div class="config-section">
                <h3>Web Server</h3>
                <div class="form-group">
                    <label for="web_port">Port Number:</label>
                    <input type="number" id="web_port" name="web_port" min="1" max="65535" value="{{ config.get('Web', 'port', fallback='505') }}">
                </div>
            </div>
            <!-- Debug Section -->
            <div class="config-section">
                <h3>Debug Options</h3>
                <div class="form-group checkbox">
                    <input type="checkbox" id="enable_cos_override" name="enable_cos_override" {% if config.get('Debug', 'enable_cos_override', fallback='false').lower() == 'true' %}checked{% endif %}>
                    <label for="enable_cos_override">Enable COS Override</label>
                </div>
                <div class="form-group checkbox">
                    <input type="checkbox" id="enable_debug_logging" name="enable_debug_logging" {% if config.get('Debug', 'enable_debug_logging', fallback='false').lower() == 'true' %}checked{% endif %}>
                    <label for="enable_debug_logging">Enable Debug Logging</label>
                </div>
            </div>
        </div>
        <button type="submit" onclick="return confirm('Save configuration changes?')">Save Configuration</button>
    </form>
</div>
</body>
</html>
'''

STATE_BLOCKS_TEMPLATE = '''
<h2>Rotation Bases State</h2>
<pre class="stateblock stateblock-1">{% for l in state.get('rotation_bases_lines', []) %}{{ l }}
{% endfor %}</pre>
<h2>Random Bases State</h2>
<pre class="stateblock stateblock-2">{% for l in state.get('random_bases_lines', []) %}{{ l }}
{% endfor %}</pre>
<h2>SudoRandom Bases State</h2>
<pre class="stateblock stateblock-3">{% for l in state.get('sudo_bases_lines', []) %}{{ l }}
{% endfor %}</pre>
<h2>Alternate-Series State</h2>
<pre class="stateblock stateblock-4">{% for l in state.get('alt_bases_lines', []) %}{{ l }}
{% endfor %}</pre>
'''

LOGIN_TEMPLATE = '''
<!doctype html>
<html>
<head>
<title>Login - DRX Dashboard</title>
<link href="https://fonts.googleapis.com/css?family=Roboto:400,700&display=swap" rel="stylesheet">
<style>
body { font-family: 'Roboto', Arial, sans-serif; background: #f5f7fa; margin: 0; padding: 0; }
#login-card { background: #fff; max-width: 900px; margin: 80px auto 40px auto; border-radius: 16px; box-shadow: 0 8px 32px 0 rgba(31, 38, 135, 0.1); padding: 2.2em 2.5em 2em 2.5em; text-align: center;}
#logo-img { max-width: 60%; height: auto; display: block; margin: 0 auto 1.2em auto; border-radius: 10px; box-shadow: 0 2px 10px 0 rgba(31, 38, 135, 0.07);}
h1 { color: #3949ab; font-weight: 700; margin-bottom: 1.2em; text-align: center; letter-spacing: 1px;}
</style>
</head>
<body>
<div id="login-card">
<img id="logo-img" src="{{ url_for('static', filename='xpander.png') }}" alt="XPANDER Digital Repeater Logo">
<h1>DRX Dashboard Login</h1>
<form method="POST" action="{{ url_for('login') }}">
    <label>Username:</label>
    <input type="text" name="username" autofocus required>
    <label>Password:</label>
    <input type="password" name="password" required>
    <button type="submit">Login</button>
</form>
{% if error %}
<div class="error-msg">{{ error }}</div>
{% endif %}
</div>
</body>
</html>
'''

def get_all_sound_files():
    try:
        ext = SOUND_FILE_EXTENSION.lower()
        return sorted([f for f in os.listdir(SOUND_DIRECTORY) if f.lower().endswith(ext)])
    except Exception:
        return []

def get_config_file_content():
    try:
        with open(config_file_path, 'r') as f:
            return f.read()
    except Exception:
        return ""

def save_config_file(new_content):
    with open(config_file_path, 'w') as f:
        f.write(new_content)

def require_login(f):
    from functools import wraps
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get('logged_in'):
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated

import sys  # Add this if not already there

# Add these variables at the top near your other imports
_state_cache = {}
_state_cache_time = 0
_state_cache_ttl = 1.0  # Cache valid for 1 second

def read_state():
    """
    Fetch current state from drx_main.py via HTTP API instead of reading from file.
    Falls back to cached state if HTTP request fails.
    """
    global _state_cache, _state_cache_time
    
    # Return cached state if it's recent enough
    if time.time() - _state_cache_time < _state_cache_ttl and _state_cache:
        return _state_cache
    
    try:
        # Fetch state from drx_main.py HTTP API
        response = requests.get(DRX_MAIN_API_URL, timeout=HTTP_TIMEOUT)
        if response.status_code == 200:
            state = response.json()
            # Update cache
            _state_cache = state
            _state_cache_time = time.time()
            return state
        else:
            # HTTP error - return cached state if available
            return _state_cache if _state_cache else {}
    except requests.exceptions.RequestException:
        # Connection failed - drx_main.py might not be running
        # Return cached state if available, otherwise empty dict
        return _state_cache if _state_cache else {}
    except Exception:
        # Other error - return cached state if available
        return _state_cache if _state_cache else {}

def write_webcmd(cmd_dict):
    with open(WEBCMD_FILE, 'w') as f:
        json.dump(cmd_dict, f)

def wait_cmd_processed(timeout=3.0):
    for _ in range(int(timeout * 10)):
        if not os.path.exists(WEBCMD_FILE):
            return
        time.sleep(0.1)

import re

def load_recent_web_log(n=10):
    try:
        if not os.path.exists(LOG_FILE):
            return ["Log file not found."]
        with open(LOG_FILE, 'r') as f:
            lines = f.readlines()
            lines = [line.rstrip() for line in lines if line.strip()]
        if not lines:
            return ["Log file is empty."]
        # Remove milliseconds from timestamps if present
        return [re.sub(r"\.\d{1,6}", "", line) for line in lines[-n:]]
    except Exception as e:
        return [f"Error loading log: {e}"]

@app.route("/login", methods=["GET", "POST"])
def login():
    error = ""
    if request.method == "POST":
        username = request.form.get("username", "")
        password = request.form.get("password", "")
        if username == WEB_USER and password == WEB_PASS:
            session['logged_in'] = True
            session['username'] = username
            return redirect(url_for('dashboard'))
        else:
            error = "Invalid username or password."
    return render_template_string(LOGIN_TEMPLATE, error=error)

@app.route("/logout", methods=["POST"])
def logout():
    session.clear()
    return redirect(url_for('login'))

@app.route("/", methods=["GET"])
@require_login
def dashboard():
    # Reload config from file each time dashboard is loaded
    global config
    config = configparser.ConfigParser()
    config.read(config_file_path)
    
    state = read_state()
    state_blocks_html = render_template_string(STATE_BLOCKS_TEMPLATE, state=state)
    
    # --- Weather System Status ---
    weather_status, weather_class, weather_color = get_weather_system_status()
    
    return render_template_string(DASHBOARD_TEMPLATE,
        currently_playing=state.get("currently_playing"),
        last_played=state.get("last_played"),
        playback_status=state.get("playback_status"),
        cos_state="YES" if is_cos_active() else "NO",
        serial_port_missing=state.get("serial_port_missing", False),
        sound_card_missing=state.get("sound_card_missing", False),
        serial_history=state.get("serial_history", []),
        state=state,
        web_log=load_recent_web_log(10)[::-1],
        all_files=get_all_sound_files(),
        config=config,  # This will now be the freshly loaded config
        session=session,
        state_blocks_html=state_blocks_html,
        drx_uptime=get_drx_uptime(),
        # Add weather system status to template
        weather_status=weather_status,
        weather_class=weather_class,
        weather_color=weather_color
    )
def is_cos_active():
    try:
        state = read_state()
        return state.get("cos_active", False)
    except Exception:
        return False

def get_drx_uptime():
    state = read_state()
    updated_at = state.get('updated_at')
    if not updated_at:
        return "drx_main.py Not Running!"
    if time.time() - float(updated_at) > 2.5:  # If no update in last 2.5 seconds
        return "drx_main.py Not Running!"
    start_time = state.get('drx_start_time')
    if not start_time:
        return "Unknown"
    uptime_seconds = int(time.time() - float(start_time))
    days, remainder = divmod(uptime_seconds, 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes, seconds = divmod(remainder, 60)
    if days > 0:
        return f"{days}d {hours}h {minutes}m {seconds}s"
    elif hours > 0:
        return f"{hours}h {minutes}m {seconds}s"
    elif minutes > 0:
        return f"{minutes}m {seconds}s"
    else:
        return f"{seconds}s"

@app.route("/stop", methods=['POST'])
@require_login
def stop_playback():
    write_webcmd({"type": "stop"})
    wait_cmd_processed()
    return redirect(url_for('dashboard'))

@app.route("/playtrack", methods=['POST'])
@require_login
def play_track():
    track_dropdown = request.form.get("track_dropdown")
    track_input = request.form.get("track_input", "").strip()
    play_method = request.form.get("play_method", "normal")
    track = track_dropdown or track_input
    if not track:
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return jsonify({"success": False, "error": "No track selected"})
        return redirect(url_for('dashboard'))

    if play_method == "local":
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return jsonify({"success": True, "local_url": url_for('play_local', filename=track)})
        return redirect(url_for('play_local', filename=track))
    else:
        write_webcmd({"type": "play", "input": track})
        wait_cmd_processed()
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return jsonify({"success": True})
        return redirect(url_for('dashboard'))

@app.route('/localplay/<filename>')
@require_login
def play_local(filename):
    return render_template_string('''
        <h2>Playing {{ filename }}</h2>
        <audio controls autoplay>
          <source src="{{ url_for('get_audio', filename=filename) }}" type="audio/wav">
          Your browser does not support the audio element.
        </audio>
        <br><a href="{{ url_for('dashboard') }}">Back to Dashboard</a>
    ''', filename=filename)

@app.route('/audio/<filename>')
@require_login
def get_audio(filename):
    return send_from_directory(SOUND_DIRECTORY, filename)

@app.route("/restart", methods=['POST'])
@require_login
def restart_script_web():
    write_webcmd({"type": "restart"})
    wait_cmd_processed()
    return redirect(url_for('dashboard'))

@app.route("/reboot", methods=['POST'])
@require_login
def reboot_system_web():
    write_webcmd({"type": "reboot"})
    wait_cmd_processed()
    return redirect(url_for('dashboard'))

@app.route("/reloadconfig", methods=['POST'])
@require_login
def reload_config_web():
    # Send command to DRX script
    write_webcmd({"type": "reload_config"})
    wait_cmd_processed()
    
    # Also reload config in the web app
    global config
    config = configparser.ConfigParser()
    config.read(config_file_path)
    
    # Flash a message to confirm
    flash('Configuration reloaded!')
    
    return redirect(url_for('dashboard'))

@app.route("/editconfig", methods=['POST'])
@require_login
def edit_config():
    content = request.form.get("config_content", "")
    if content:
        save_config_file(content)
        write_webcmd({"type": "reload_config"})
        wait_cmd_processed()
    return redirect(url_for('dashboard'))

@app.route("/api/log_entries")
@require_login
def api_log_entries():
    entries = load_recent_web_log(10)[::-1]
    return render_template_string('''
        {% for entry in web_log %}
            <div>{{ entry }}</div>
        {% endfor %}
    ''', web_log=entries)

@app.route("/api/serial_commands")
@require_login
def api_serial_commands():
    state = read_state()
    serial_history = state.get("serial_history", [])
    def fmt(entry):
        ts = entry.get("ts", "")
        cmd = entry.get("cmd", "")
        src = entry.get("src", "Serial")
        return f"{ts}: {cmd} ({src})"
    formatted = [fmt(entry) for entry in serial_history[:5]]
    return render_template_string('''
        {% for line in formatted %}
            <div>{{ line }}</div>
        {% endfor %}
    ''', formatted=formatted)

@app.route("/api/state_blocks")
@require_login
def api_state_blocks():
    state = read_state()
    return render_template_string(STATE_BLOCKS_TEMPLATE, state=state)

@app.route("/api/drx_uptime")
@require_login
def api_drx_uptime():
    state = read_state()
    updated_at = state.get('updated_at')
    not_running = False
    if not updated_at or time.time() - float(updated_at) > 2.5:
        not_running = True
    return jsonify({
        'drx_uptime': get_drx_uptime(),
        'not_running': not_running,
    })

@app.route("/api/message_timer")
@require_login
def api_message_timer():
    state = read_state()
    last_played = state.get("message_timer_last_played", 0)
    timer_value = state.get("message_timer_value", 0)
    now = time.time()
    if last_played and timer_value:
        seconds_left = int(max(0, timer_value * 60 - (now - last_played)))
    else:
        seconds_left = 0
    return jsonify({"seconds_left": seconds_left})

@app.route("/editconfig_structured", methods=['POST'])
@require_login
def edit_config_structured():
    # Read the existing config to preserve structure
    existing_config = configparser.ConfigParser()
    existing_config.read(config_file_path)
    
    # Get form data
    form_data = request.form.to_dict()
    
    # Update Serial section
    if 'Serial' not in existing_config:
        existing_config['Serial'] = {}
    existing_config['Serial']['port'] = form_data.get('serial_port', '/dev/ttyUSB0')
    existing_config['Serial']['baudrate'] = form_data.get('serial_baudrate', '57600')
    existing_config['Serial']['timeout'] = form_data.get('serial_timeout', '0.5')
    existing_config['Serial']['line_timeout'] = form_data.get('serial_line_timeout', '2.0')
    
    # Update Sound section
    if 'Sound' not in existing_config:
        existing_config['Sound'] = {}
    existing_config['Sound']['directory'] = form_data.get('sound_directory', '/home/brian/DRX/sounds/')
    existing_config['Sound']['extension'] = form_data.get('sound_extension', '.wav')
    existing_config['Sound']['device'] = form_data.get('sound_device', 'plughw:2,0')
    
    # Update General section
    if 'General' not in existing_config:
        existing_config['General'] = {}
    existing_config['General']['Message Timer'] = form_data.get('message_timer', '10')
    
    # Update Rotation section
    if 'Rotation' not in existing_config:
        existing_config['Rotation'] = {}
    existing_config['Rotation']['base'] = form_data.get('rotation_base', '5300')
    existing_config['Rotation']['end'] = form_data.get('rotation_end', '5331')
    existing_config['Rotation']['interval'] = form_data.get('rotation_interval', '0')
    
    # Update Random section
    if 'Random' not in existing_config:
        existing_config['Random'] = {}
    existing_config['Random']['base'] = form_data.get('random_base', '5400')
    existing_config['Random']['end'] = form_data.get('random_end', '5432')
    existing_config['Random']['interval'] = form_data.get('random_interval', '0')
    
    # Update SudoRandom section
    if 'SudoRandom' not in existing_config:
        existing_config['SudoRandom'] = {}
    existing_config['SudoRandom']['base'] = form_data.get('sudorandom_base', '5600')
    existing_config['SudoRandom']['end'] = form_data.get('sudorandom_end', '5669')
    existing_config['SudoRandom']['interval'] = form_data.get('sudorandom_interval', '10')
    
    # Update GPIO section
    if 'GPIO' not in existing_config:
        existing_config['GPIO'] = {}
    existing_config['GPIO']['cos_pin'] = form_data.get('cos_pin', '16')
    existing_config['GPIO']['cos_activate_level'] = 'True' if form_data.get('cos_activate_level') else 'False'
    existing_config['GPIO']['remote_busy_pin'] = form_data.get('remote_busy_pin', '20')
    existing_config['GPIO']['remote_busy_activate_level'] = 'True' if form_data.get('remote_busy_activate_level') else 'False'
    existing_config['GPIO']['cos_debounce_time'] = form_data.get('cos_debounce_time', '1.0')
    existing_config['GPIO']['max_cos_interruptions'] = form_data.get('max_cos_interruptions', '3')
        
    # Update WebAuth section
    if 'WebAuth' not in existing_config:
        existing_config['WebAuth'] = {}
    existing_config['WebAuth']['username'] = form_data.get('web_username', 'k1sox')
    if form_data.get('web_password'):
        existing_config['WebAuth']['password'] = form_data.get('web_password')
    
    # Update Web section
    if 'Web' not in existing_config:
        existing_config['Web'] = {}
    existing_config['Web']['port'] = form_data.get('web_port', '505')
    
    # Update Debug section
    if 'Debug' not in existing_config:
        existing_config['Debug'] = {}
    existing_config['Debug']['enable_cos_override'] = 'true' if form_data.get('enable_cos_override') else 'false'
    existing_config['Debug']['enable_debug_logging'] = 'true' if form_data.get('enable_debug_logging') else 'false'
    
    # Write to file
    with open(config_file_path, 'w') as configfile:
        existing_config.write(configfile)
    
    # Use the simpler approach for telling DRX to reload
    write_webcmd({"type": "reload_config"})
    wait_cmd_processed()
    
    flash('Configuration updated successfully!')
    return redirect(url_for('dashboard'))

@app.route("/api/status")
@require_login
def status_api():
    state = read_state()
    data = {
        "currently_playing": state.get("currently_playing"),
        "last_played": state.get("last_played"),
        "playback_status": state.get("playback_status"),
        "cos_state": is_cos_active(),
        "serial_port_missing": state.get("serial_port_missing", False),
        "sound_card_missing": state.get("sound_card_missing", False),
        "remote_device_active": state.get("remote_device_active", False),
    }
    return jsonify(data)

@app.route("/debug/ping")
def debug_ping():
    return jsonify({
        "time": time.time(),
        "formatted_time": time.strftime("%Y-%m-%d %H:%M:%S"),
        "success": True
    })

@app.route("/debug/webcmd_test")
@require_login
def debug_webcmd_test():
    try:
        # Create test command
        test_cmd = {"type": "test_command", "timestamp": time.time()}
        write_webcmd(test_cmd)
        
        # Wait a bit
        time.sleep(0.5)
        
        # Check if file still exists
        file_exists = os.path.exists(WEBCMD_FILE)
        
        return jsonify({
            "test_sent": True,
            "file_still_exists": file_exists,
            "current_time": time.strftime("%Y-%m-%d %H:%M:%S")
        })
    except Exception as e:
        return jsonify({"error": str(e)})

@app.route("/debug/config_values")
@require_login
def debug_config_values():
    # Reload config
    test_config = configparser.ConfigParser()
    test_config.read(config_file_path)
    
    # Get a few sample values
    sample_values = {
        "sound_directory": test_config.get('Sound', 'directory', fallback='Not found'),
        "web_port": test_config.get('Web', 'port', fallback='Not found'),
        "rotation_base": test_config.get('Rotation', 'base', fallback='Not found')
    }
    
    return jsonify({
        "config_path": config_file_path,
        "config_exists": os.path.exists(config_file_path),
        "sample_values": sample_values,
        "read_time": time.strftime("%Y-%m-%d %H:%M:%S")
    })

@app.route("/reset_minutes", methods=["POST"])
@require_login
def reset_minutes():
    # Send a reset command to DRX
    write_webcmd({"type": "reset_minutes"})
    wait_cmd_processed()
    return redirect(url_for('dashboard'))

@app.route("/api/cos_minutes")
@require_login
def api_cos_minutes():
    state = read_state()
    return jsonify({
        "cos_today_minutes": state.get("cos_today_minutes", 0),
        "cos_today_date": state.get("cos_today_date", "")
    })

@app.route("/api/dtmf_log")
def api_dtmf_log():
    n = 100  # Number of lines
    try:
        if not os.path.exists(DTMF_LOG_FILE):
            return "<div>No DTMF log entries found.</div>"
        with open(DTMF_LOG_FILE, "r") as f:
            lines = [line.rstrip() for line in f.readlines() if line.strip()]
        lines = lines[:n]
    except Exception as e:
        lines = [f"Error loading DTMF log: {e}"]
    return render_template_string('''
        {% for entry in dtmf_log %}
            <div>{{ entry }}</div>
        {% endfor %}
    ''', dtmf_log=lines)

# ... all your existing code above ...

@app.route("/download_dtmf_log")
@require_login
def download_dtmf_log():
    # DTMF log is stored in the same directory as this script.
    if not os.path.exists(DTMF_LOG_FILE):
        return "No DTMF log file.", 404
    return send_from_directory(
        os.path.dirname(DTMF_LOG_FILE),
        os.path.basename(DTMF_LOG_FILE),
        as_attachment=True
    )

def get_weather_system_status():
    wx_dir = os.path.join(os.path.dirname(__file__), "wx")
    wx_gen = os.path.join(wx_dir, "wx_gen.py")
    wx_data = os.path.join(wx_dir, "wx_data")
    if not os.path.exists(wx_gen):
        return ("Not Installed", "status-warn", "#888")
    if not os.path.exists(wx_data):
        return ("Inactive", "status-bad", "#d32f2f")
    mtime = os.path.getmtime(wx_data)
    if time.time() - mtime > 7200:
        return ("Inactive", "status-bad", "#d32f2f")
    return ("Active", "status-good", "#388e3c")

@app.route("/debug/state_file")
@require_login
def debug_state_file():
    try:
        if os.path.exists(STATE_FILE):
            file_stats = os.stat(STATE_FILE)
            with open(STATE_FILE, 'r') as f:
                state_content = f.read()
            
            return jsonify({
                "exists": True,
                "size": file_stats.st_size,
                "modified": time.ctime(file_stats.st_mtime),
                "content_length": len(state_content),
                "parse_test": json.loads(state_content) is not None
            })
        else:
            return jsonify({"exists": False})
    except Exception as e:
        return jsonify({"error": str(e)})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=WEB_PORT, debug=True, use_reloader=False)