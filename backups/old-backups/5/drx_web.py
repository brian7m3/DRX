import os
import json
import time
import configparser
from flask import Flask, render_template_string, redirect, url_for, request, session, send_from_directory, jsonify

DRX_START_TIME = time.time()

# --- Load config for credentials and port ---
script_dir = os.path.dirname(os.path.realpath(__file__))
config_file_path = os.path.join(script_dir, 'config.ini')
config = configparser.ConfigParser()
config.read(config_file_path)

WEB_PORT = int(config.get("Web", "port", fallback="8080"))
WEB_USER = config.get("WebAuth", "username", fallback="admin")
WEB_PASS = config.get("WebAuth", "password", fallback="drxpass")

STATE_FILE = '/tmp/drx_state.json'
WEBCMD_FILE = '/tmp/drx_webcmd.json'
SOUND_DIRECTORY = config['Sound']['directory']
SOUND_FILE_EXTENSION = config['Sound']['extension']
LOG_FILE = '/tmp/drx_webconsole.log'

app = Flask(__name__)
app.secret_key = "change_this_to_a_random_secret"

DASHBOARD_TEMPLATE = '''
<!doctype html>
<html>
<head>
<title>DRX Dashboard</title>
<link href="https://fonts.googleapis.com/css?family=Roboto:400,700&display=swap" rel="stylesheet">
<style>
body { font-family: 'Roboto', Arial, sans-serif; background: #f5f7fa; margin: 0; padding: 0; }
#main-card { background: #fff; max-width: 900px; margin: 40px auto; border-radius: 16px; box-shadow: 0 8px 32px 0 rgba(31,38,135,0.1); padding: 2em 2.5em; }
h1 { margin-top: 0; color: #3949ab; font-weight: 700; letter-spacing: 1px;}
h2 { color: #3949ab; border-bottom: 1px solid #e3e6f0; padding-bottom: 0.3em; }
ul { padding-left: 1.2em; }
li { margin-bottom: 0.3em; }
form { margin-bottom: 1.5em; }
input[type=text], input[type=password], select, textarea {
    font-size: 1.1em; border: 1px solid #bdbdbd; border-radius: 5px; padding: 0.4em; margin-right: 0.5em; background: #f9f9fc; color: #2d2d2d;
}
textarea { font-family: 'Roboto Mono', monospace; width: 100%; }
button {
    font-size: 1.1em; border: none; border-radius: 5px; background: linear-gradient(90deg,#3949ab,#1976d2); color: #fff; padding: 0.5em 1.2em; cursor: pointer; transition: background 0.2s; margin-top: 0.5em; margin-bottom: 0.5em;
}
button:hover { background: linear-gradient(90deg,#1976d2,#3949ab);}
#logout-btn { float: right; margin-top: 10px;}
.status-list li { font-size: 1.1em; margin-bottom: 0.7em;}
.status-good { color: #388e3c; font-weight: bold; }
.status-warn { color: #fbc02d; font-weight: bold; }
.status-bad { color: #d32f2f; font-weight: bold; }
.card-section { background: #f1f3fa; border-radius: 10px; padding: 1em 1.5em; margin-bottom: 2em; box-shadow: 0 2px 8px 0 rgba(31,38,135,0.07);}
.card-section ul, .card-section ol { margin: 0; }
.logs, .serials {
    background: #212121; color: #ececec; font-family: 'Roboto Mono', monospace; font-size: 1em; padding: 1em; border-radius: 7px; margin-top: 0.7em; margin-bottom: 1.2em; overflow-x: auto; max-height: 200px;
}
.label { background: #3949ab; color: #fff; border-radius: 4px; padding: 0.1em 0.5em; font-size: 0.95em; margin-right: 0.4em; }
@media (max-width: 650px) {
    #main-card { padding: 1em 0.5em;}
    h1 { font-size: 1.3em;}
    h2 { font-size: 1.07em;}
    .card-section { padding: 0.6em 0.4em;}
    button { width: 100%; }
}
pre.stateblock { background: #e3e6f0; color: #222; padding: 1em; border-radius: 8px; font-family: 'Roboto Mono', monospace; font-size: 1em; margin: 0 0 1em 0; white-space: pre-line;}
/* --- Flashing red animation for Not Running --- */
@keyframes flashRed {
  0%, 100% { color: #d32f2f; background: none; }
  50% { color: #fff; background: #d32f2f; }
}
.flash-red {
  animation: flashRed 1s infinite;
  font-weight: bold;
  border-radius: 6px;
  padding: 0 0.4em;
}
</style>
<style>
/* Help Modal Styles */
.modal {
  display: none; position: fixed; z-index: 999; left: 0; top: 0; width: 100vw; height: 100vh;
  overflow: auto; background-color: rgba(0,0,0,0.4);
}
.modal-content {
  background: #fefefe; margin: 7% auto; padding: 2em; border: 1px solid #888; width: 90%; max-width: 450px; border-radius: 10px; box-shadow: 0 4px 20px #3333;
}
.close {
  color: #3949ab; float: right; font-size: 2em; font-weight: bold; cursor: pointer;
}
.close:hover { color: #d32f2f; }
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

    // Poll status every second
    updateStatus = function() {
        fetch("{{ url_for('status_api') }}", {credentials: 'same-origin'})
        .then(response => response.json())
        .then(data => {
            document.querySelectorAll('.status-currently-playing').forEach(function(el) {
                el.textContent = data.currently_playing || "None";
            });
            if (document.getElementById('playback-status')) {
                document.getElementById('playback-status').textContent = data.playback_status || "Idle";
            }
            if (document.getElementById('cos-state')) {
                document.getElementById('cos-state').textContent = data.cos_state ? "YES" : "NO";
                document.getElementById('cos-state').className = data.cos_state ? "status-good" : "status-warn";
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

    setInterval(updateStatus, 1000);
    setInterval(updateSerialSection, 1000);
    setInterval(updateLogsSection, 1000);
    setInterval(updateStateSection, 1000);

    updateStatus();
    updateSerialSection();
    updateLogsSection();
    updateStateSection();
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
    <div class="card-section">
        <ul class="status-list">
            <li>
                <b>Currently Playing:</b>
                <span class="status-good status-currently-playing">{{ currently_playing or "None" }}</span>
                <form method="POST" action="{{ url_for('stop_playback') }}" style="display:inline; margin-left:1em;">
                    <button type="submit" style="font-size:0.95em;padding:0.25em 0.8em;">Stop Playback</button>
                </form>
            </li>
            <li><b>Status:</b> <span id="playback-status">{{ playback_status or "Idle" }}</span></li>
            <li><b>COS Active:</b>
                <span id="cos-state" class="{% if cos_state == 'YES' %}status-good{% else %}status-warn{% endif %}">{{ cos_state }}</span>
            </li>
            <li><b>Serial Port:</b>
                {% if not serial_port_missing %}
                    <span class="status-good">OK</span>
                {% else %}
                    <span class="status-bad">Missing</span>
                {% endif %}
            </li>
            <li><b>Sound Card:</b>
                {% if not sound_card_missing %}
                    <span class="status-good">OK</span>
                {% else %}
                    <span class="status-bad">Missing</span>
                {% endif %}
            </li>
        </ul>
    </div>
    <div class="card-section" id="state-section">
        {{ state_blocks_html|safe }}
    </div>
<div class="card-section">
    <!-- Duplicate Currently Playing here -->
    <b>Currently Playing:</b>
    <span class="status-good status-currently-playing">{{ currently_playing or "None" }}</span>
    <br>
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

    <!-- Help Modal HTML -->
    <div id="help-modal" class="modal">
      <div class="modal-content">
        <span class="close" id="close-help">&times;</span>
        <h2>Help: Playing Tracks</h2>
        <ul>
          <li><b>Play Specific Track:</b> Use the dropdown to select a track or use the input to specify one directly.</li>
          <li><b>Play Method:</b> 
            <ul>
              <li><b>Normal (DRX):</b> Sends the play command to the DRX backend, which plays it on the repeater.</li>
              <li><b>Local (Web Page):</b> Plays the audio in your web browser only (does not transmit on repeater).You must enter the full file name in input box (1001.wav).</li>
            </ul>
          </li>
          <li>If you input serial data, use the correct format as expected by your DRX system.</li>
          <li>You can use either method to play tracks, but only one at a time.</li>
          <ul><h3>Serial Functions when COS becomes Active:</h3></ul>
          <b><center>ALL TRACKS MUST START WITH a P !</center></b>
          <hr>
            <ul>
              <li><b>I:</b> Interrupts the wav - P1001I.</li>
              <li><b>R:</b> Repeats the wav - P1001R.  Repeats x number of times until plays through. *</li>
              <li><b>P:</b> Pauses the wav - P1001P.  Pauses x number of times before giving up. *</li>
              <li><b>A:</b> Alternates between Bases.  P4000A5000A6000R (example with Repeat suffix).  This can cross base types.</li>
              <li><b>i:</b> Interrupts primary wav and immediately plays secondary even if COS active - P3050i3000</li>
          <h3>Bases:</h3> 
              A base type is called by sending P&lt;base #&gt;.  <br>Example: config.ini defines rotating base as base=4200,end=4210,interval=5, P4200, will play 4201.wav and cycle to 4202.wav after 5 minutes.  This will continue and loop back to 4201.
              <br>
              <br>
              <li><b>Rotation</b> Rotations through the tracks in the base +1 to end tracks. Changes to next track at interval expiration. *</li>
              <li><b>Random</b> Randomly plays the tracks in the base +1 to end tracks. Changes to next track at interval expiration. *</li>
              <li><b>SudoRandom</b> Pseudo random - same as random except plays all tracks in a base group before playing again.  Won't play same track in 2 cycles. *</li>
              * defined in config.ini file.</li>
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
        <form method="POST" action="{{ url_for('restart_script_web') }}">
            <button type="submit" onclick="return confirm('Restart the DRX script?')">Restart DRX Script</button>
        </form>
        <form method="POST" action="{{ url_for('reboot_system_web') }}">
            <button type="submit" onclick="return confirm('Reboot the system?')">Reboot System</button>
        </form>
        <form method="POST" action="{{ url_for('reload_config_web') }}">
            <button type="submit">Reload Configuration File</button>
        </form>
    </div>
    <div class="card-section">
        <h2>Edit config.ini</h2>
        <form method="POST" action="{{ url_for('edit_config') }}">
            <textarea name="config_content" rows="16" cols="80">{{ config_content }}</textarea><br>
            <button type="submit" onclick="return confirm('Save changes to config.ini?')">Save Config</button>
        </form>
    </div>
</div>
<script>
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
document.addEventListener("DOMContentLoaded", function() {
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
</script>
</body>
</html>
'''

STATE_BLOCKS_TEMPLATE = '''
<h2>Random Bases State</h2>
<pre class="stateblock">{% for l in state.get('random_bases_lines', []) %}{{ l }}
{% endfor %}</pre>
<h2>Rotation Bases State</h2>
<pre class="stateblock">{% for l in state.get('rotation_bases_lines', []) %}{{ l }}
{% endfor %}</pre>
<h2>SudoRandom Bases State</h2>
<pre class="stateblock">{% for l in state.get('sudorandom_bases_lines', []) %}{{ l }}
{% endfor %}</pre>
<h2>Alternate-Series State</h2>
<pre class="stateblock">{% for l in state.get('alt_bases_lines', []) %}{{ l }}
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

def read_state():
    try:
        with open(STATE_FILE, 'r') as f:
            return json.load(f)
    except Exception:
        return {}

def write_webcmd(cmd_dict):
    with open(WEBCMD_FILE, 'w') as f:
        json.dump(cmd_dict, f)

def wait_cmd_processed(timeout=3.0):
    for _ in range(int(timeout * 10)):
        if not os.path.exists(WEBCMD_FILE):
            return
        time.sleep(0.1)

def load_recent_web_log(n=10):
    try:
        if not os.path.exists(LOG_FILE):
            return ["Log file not found."]
        with open(LOG_FILE, 'r') as f:
            lines = f.readlines()
            lines = [line.rstrip() for line in lines if line.strip()]
        if not lines:
            return ["Log file is empty."]
        return lines[-n:]
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
    state = read_state()
    state_blocks_html = render_template_string(STATE_BLOCKS_TEMPLATE, state=state)
    return render_template_string(DASHBOARD_TEMPLATE,
        currently_playing=state.get("currently_playing"),
        playback_status=state.get("playback_status"),
        cos_state="YES" if is_cos_active() else "NO",
        serial_port_missing=state.get("serial_port_missing", False),
        sound_card_missing=state.get("sound_card_missing", False),
        serial_history=state.get("serial_history", []),
        state=state,
        web_log=load_recent_web_log(10),
        all_files=get_all_sound_files(),
        config_content=get_config_file_content(),
        session=session,
        state_blocks_html=state_blocks_html,
        drx_uptime=get_drx_uptime()
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
    write_webcmd({"type": "reload_config"})
    wait_cmd_processed()
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

@app.route("/api/status")
@require_login
def status_api():
    state = read_state()
    data = {
        "currently_playing": state.get("currently_playing"),
        "playback_status": state.get("playback_status"),
        "cos_state": is_cos_active(),
        "serial_port_missing": state.get("serial_port_missing", False),
        "sound_card_missing": state.get("sound_card_missing", False),
    }
    return jsonify(data)

@app.route("/api/log_entries")
@require_login
def api_log_entries():
    entries = load_recent_web_log(10)
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
    return render_template_string('''
        {% for cmd in serial_history[:5] %}
            <div>{{ cmd }}</div>
        {% endfor %}
    ''', serial_history=serial_history)

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

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=WEB_PORT, debug=True, use_reloader=False)