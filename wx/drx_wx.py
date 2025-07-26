import os
import sys
import json
import time
import shutil
import csv
import re
import configparser
from datetime import datetime
import threading
import requests
from urllib.request import urlopen, Request
from urllib.error import URLError, HTTPError
from bs4 import BeautifulSoup

# ---- CONFIGURATION ----

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(SCRIPT_DIR, "wx_config.ini")
SAME_CSV = os.path.join(SCRIPT_DIR, "same.csv")

def load_config():
    config = configparser.ConfigParser()
    config.read(CONFIG_FILE)

    wx_cfg = config['weather']
    wx_polling_time = int(wx_cfg.get('polling_time', '15')) * 60
    wx_data_url = wx_cfg.get('wx_data_url', '')
    wx_day_url = wx_cfg.get('wx_day_url', '')
    nws_url = wx_cfg.get('nws_url', '')
    nws_url_fallback = wx_cfg.get('nws_url_fallback', '')
    wx_directory = wx_cfg.get("directory", "/home/drx/DRX/wx/")
    use_nws_only = wx_cfg.get('use_nws_only', 'false').lower() == 'true'

    same_cfg = config['SAME Alerts']
    same_zip = same_cfg.get('zip_code', '').strip()
    same_polling_time = int(same_cfg.get('polling_time', '300'))
    same_user_agent = same_cfg.get('user_agent', 'WX-SAME-Script')

    return {
        "wx": {
            "directory": wx_directory,
            "polling_time": wx_polling_time,
            "wx_data_url": wx_data_url,
            "wx_day_url": wx_day_url,
            "nws_url": nws_url,
            "nws_url_fallback": nws_url_fallback,
            "use_nws_only": use_nws_only,
        },
        "same": {
            "zip_code": same_zip,
            "polling_time": same_polling_time,
            "user_agent": same_user_agent
        }
    }

# ---- WEATHER LOGIC ----

def get_json(url):
    try:
        resp = requests.get(url, timeout=15)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        print(f"[weather] Error fetching JSON: {e}", file=sys.stderr)
        return {}

def get_nested(data, keys, default=None):
    for key in keys:
        if isinstance(data, list):
            if not data:
                return default
            data = data[-1]
        data = data.get(key) if isinstance(data, dict) else default
        if data is None:
            return default
    return data

def degrees_to_direction(degrees):
    try:
        deg = int(degrees)
    except (ValueError, TypeError):
        return "Unknown"
    dirs = [
        (0, 11, "N"), (11, 34, "NNE"), (34, 56, "NE"), (56, 79, "ENE"),
        (79, 101, "E"), (101, 124, "ESE"), (124, 146, "SE"), (146, 169, "SSE"),
        (169, 191, "S"), (191, 214, "SSW"), (214, 236, "SW"), (236, 259, "WSW"),
        (259, 281, "W"), (281, 304, "WNW"), (304, 326, "NW"), (326, 349, "NNW"),
        (349, 361, "N")
    ]
    for start, end, label in dirs:
        if start <= deg < end:
            return label
    return "Invalid"

def read_pressure_from_file(filepath):
    if not os.path.exists(filepath):
        return None
    with open(filepath, "r") as f:
        for line in f:
            if line.startswith("pressure: "):
                try:
                    value = line.split(":", 1)[1].strip().split()[0]
                    return float(value)
                except Exception:
                    return None
    return None

def fetch_nws_obhistory_all_fields(obhistory_url, fallback_url):
    result = {
        "observations": "Unknown",
        "temperature": "Unknown",
        "humidity": "Unknown",
        "winddir": "Unknown",
        "wind_speed": "Unknown",
        "wind_gust": "Unknown",
        "pressure": "Unknown",
        "pressure_status": "unknown",
        "visibility": "Unknown",
        "precipRate": "Unknown"
    }

    try:
        resp = requests.get(obhistory_url, timeout=15)
        resp.raise_for_status()
        html = resp.text
        soup = BeautifulSoup(html, "html.parser")
        table = soup.find("table")
        if not table:
            raise Exception("Could not find table in NWS obhistory page.")
        rows = table.find_all("tr")
        if len(rows) < 2:
            raise Exception("Not enough rows in obhistory table.")

        header_cells = [cell.get_text(strip=True) for cell in rows[0].find_all(["th", "td"])]
        data_row = None
        for row in rows[1:]:
            data_cells = [cell.get_text(strip=True) for cell in row.find_all("td")]
            if data_cells and len(data_cells) >= 4:
                while len(data_cells) < len(header_cells):
                    data_cells.append("Unknown")
                data_row = data_cells
                break
        if not data_row:
            raise Exception("No data rows found in obhistory table.")

        row_map = {header: val for header, val in zip(header_cells, data_row)}

        wind_val = row_map.get("Wind (mph)", "Unknown")
        winddir = "Unknown"
        wind_speed = "Unknown"
        if wind_val != "Unknown":
            wind_match = re.match(r"([A-Za-z]+)?\s*(\d+)?", wind_val.replace('\n', ' ').strip())
            if wind_match:
                winddir = wind_match.group(1) if wind_match.group(1) else "Unknown"
                wind_speed = wind_match.group(2) if wind_match.group(2) else "Unknown"
            else:
                winddir = wind_val
                wind_speed = wind_val

        humidity = row_map.get("Pressure", "Unknown")
        if "%" not in humidity:
            for cell in data_row:
                if "%" in cell:
                    humidity = cell
                    break

        if humidity != "Unknown":
            humidity = humidity.replace("%", "").strip() + " percent"

        pressure = "Unknown"
        try:
            precip_idx = header_cells.index("Precipitation (in)")
            for cell in data_row[precip_idx+1:]:
                if re.match(r'^(2[8-9]|3[0-2])\.\d{2}$', cell.strip()):
                    pressure = cell.strip()
                    break
        except Exception:
            for cell in data_row:
                if re.match(r'^(2[8-9]|3[0-2])\.\d{2}$', cell.strip()):
                    pressure = cell.strip()
                    break

        result.update({
            "observations": row_map.get("Weather", "Unknown"),
            "temperature": row_map.get("Temperature (ºF)", "Unknown"),
            "humidity": humidity,
            "winddir": winddir,
            "wind_speed": wind_speed,
            "pressure": pressure,
            "visibility": row_map.get("Vis. (mi.)", "Unknown"),
            "precipRate": row_map.get("Precipitation (in)", "Unknown"),
        })

        return result
    except Exception as e:
        print(f"[weather] Error fetching NWS obhistory (table): {e}", file=sys.stderr)
        print("[weather] Falling back to MapClick parser...", file=sys.stderr)
        return result

def weather_worker(wx_cfg):
    directory = wx_cfg["directory"]
    nws_url = wx_cfg["nws_url"]
    nws_url_fallback = wx_cfg.get("nws_url_fallback", "")
    wx_data_url = wx_cfg["wx_data_url"]
    wx_day_url = wx_cfg["wx_day_url"]
    polling_time = wx_cfg["polling_time"]
    use_nws_only = wx_cfg.get("use_nws_only", False)
    output_file = os.path.join(directory, "wx_data")
    backup_file = os.path.join(directory, "wx_data_previous")
    timestamp_file = os.path.join(directory, "wx_data_previous_time")

    os.makedirs(directory, exist_ok=True)

    while True:
        current_time = time.time()
        backup_needed = True
        if os.path.exists(output_file):
            if os.path.exists(timestamp_file):
                try:
                    with open(timestamp_file, "r") as tf:
                        last_backup_time = float(tf.read().strip())
                    if current_time - last_backup_time < 7200:
                        backup_needed = False
                except Exception:
                    pass
            if backup_needed:
                try:
                    shutil.copy2(output_file, backup_file)
                    with open(timestamp_file, "w") as tf:
                        tf.write(str(current_time))
                except Exception as e:
                    print(f"[weather] Backup error: {e}", file=sys.stderr)

        if use_nws_only:
            obhistory_result = fetch_nws_obhistory_all_fields(nws_url, nws_url_fallback)
            previous_pressure = read_pressure_from_file(backup_file)
            new_pressure = obhistory_result.get("pressure")
            baro_status = "unknown"
            try:
                prev = float(previous_pressure) if previous_pressure is not None else None
                new = float(new_pressure) if new_pressure not in (None, "Unknown") else None
                if prev is not None and new is not None:
                    if prev > new:
                        baro_status = "falling"
                    elif prev < new:
                        baro_status = "rising"
                    else:
                        baro_status = "steady"
            except Exception:
                baro_status = "unknown"

            output_data = {}
            for key in [
                "observations", "temperature", "humidity", "winddir", "wind_speed",
                "wind_gust", "pressure", "visibility", "precipRate"
            ]:
                val = obhistory_result.get(key, "Unknown")
                output_data[key] = val
            output_data["pressure_status"] = baro_status
        else:
            obhistory_result = fetch_nws_obhistory_all_fields(nws_url, nws_url_fallback)
            wx_data_json = get_json(wx_data_url)
            wx_day_json = get_json(wx_day_url)
            observations = wx_day_json.get("observations", [])
            last_obs = observations[-1] if observations else {}

            temperature = get_nested(last_obs, ["imperial", "tempAvg"])
            humidity = get_nested(last_obs, ["humidityAvg"])
            winddir_deg = get_nested(last_obs, ["winddirAvg"])
            wind_speed = get_nested(last_obs, ["imperial", "windspeedAvg"])
            wind_gust = get_nested(last_obs, ["imperial", "windgustAvg"])
            pressure = get_nested(last_obs, ["imperial", "pressureMax"])

            precip_rate = None
            if wx_data_json.get("observations"):
                obs0 = wx_data_json["observations"][0]
                precip_rate = obs0.get("imperial", {}).get("precipRate")
            winddir = degrees_to_direction(winddir_deg)
            previous_pressure = read_pressure_from_file(backup_file)
            new_pressure = pressure
            baro_status = "unknown"
            if previous_pressure is not None and new_pressure is not None:
                try:
                    previous_pressure = float(previous_pressure)
                    new_pressure = float(new_pressure)
                    if previous_pressure > new_pressure:
                        baro_status = "falling"
                    elif previous_pressure < new_pressure:
                        baro_status = "rising"
                    else:
                        baro_status = "steady"
                except Exception:
                    baro_status = "unknown"

            output_data = {
                "observations": obhistory_result.get("observations", "Unknown"),
                "temperature": f"{temperature} degrees" if temperature is not None else obhistory_result.get("temperature", "Unknown"),
                "humidity": f"{humidity} percent" if humidity is not None else obhistory_result.get("humidity", "Unknown"),
                "winddir": winddir if winddir != "Unknown" else obhistory_result.get("winddir", "Unknown"),
                "wind_speed": f"{wind_speed}" if wind_speed is not None else obhistory_result.get("wind_speed", "Unknown"),
                "wind_gust": f"{wind_gust}" if wind_gust is not None else obhistory_result.get("wind_gust", "Unknown"),
                "pressure": f"{pressure}" if pressure is not None else obhistory_result.get("pressure", "Unknown"),
                "pressure_status": baro_status,
                "visibility": obhistory_result.get("visibility", "Unknown"),
                "precipRate": f"{precip_rate:.2f}" if precip_rate is not None else obhistory_result.get("precipRate", "Unknown")
            }

        try:
            with open(output_file, "w") as f:
                for key, val in output_data.items():
                    f.write(f"{key}: {val}\n")
            os.chmod(output_file, 0o666)
            print(f"[weather] Weather data written to {output_file}")
        except Exception as e:
            print(f"[weather] Error writing weather data: {e}", file=sys.stderr)

        time.sleep(polling_time)

# ---- SAME ALERTS LOGIC ----

def load_eas_descriptions():
    descriptions = {}
    try:
        with open(SAME_CSV, 'r') as f:
            reader = csv.reader(f)
            for row in reader:
                if len(row) >= 2:
                    description = row[0].strip()
                    code = row[1].strip()
                    descriptions[code] = description
    except FileNotFoundError:
        print(f"Warning: same.csv not found in {SCRIPT_DIR}")
        print("Falling back to API descriptions.")
    except Exception as e:
        print(f"Warning: Error reading same.csv: {e}")
        print("Falling back to API descriptions.")
    return descriptions

def parse_nws_date(date_string):
    if not date_string:
        return "N/A"
    try:
        dt_obj = datetime.fromisoformat(date_string)
        return dt_obj.strftime('%Y-%m-%d %H:%M:%S')
    except (ValueError, TypeError):
        return date_string

def get_nws_zone_from_zip(zip_code, user_agent):
    headers = {'User-Agent': user_agent}
    geocode_url = f"https://api.zippopotam.us/us/{zip_code}"
    try:
        with urlopen(Request(geocode_url, headers=headers), timeout=10) as response:
            geocode_data = json.load(response)
            lat = geocode_data['places'][0]['latitude']
            lon = geocode_data['places'][0]['longitude']
    except (HTTPError, URLError, KeyError, IndexError) as e:
        return None, f"Could not get location data for ZIP {zip_code}. Reason: {e}"

    points_url = f"https://api.weather.gov/points/{lat},{lon}"
    try:
        with urlopen(Request(points_url, headers=headers), timeout=10) as response:
            points_data = json.load(response)
            forecast_zone_url = points_data.get('properties', {}).get('forecastZone')
            if forecast_zone_url:
                zone_id = forecast_zone_url.split('/')[-1]
                return zone_id, None
            else:
                return None, "Could not determine NWS forecast zone from API response."
    except (HTTPError, URLError) as e:
        return None, f"Could not contact NWS API for zone info. Reason: {e}"
    except KeyError:
        return None, "NWS API response for zone info was in an unexpected format."

def extract_same_code(properties):
    event_code = properties.get('eventCode')
    all_codes = []
    if isinstance(event_code, dict):
        same_codes = event_code.get('SAME', [])
        if same_codes and isinstance(same_codes, list):
            all_codes.extend(same_codes)
        if not all_codes:
            nws_codes = event_code.get('NationalWeatherService', [])
            if nws_codes and isinstance(nws_codes, list):
                all_codes.extend(nws_codes)
    elif isinstance(event_code, str):
        all_codes.append(event_code)
    return ', '.join(all_codes) if all_codes else 'N/A'

def get_original_same_code(alert, all_alerts):
    properties = alert.get('properties', {})
    refs = properties.get('references', [])
    if not refs:
        return None
    ref_ids = {ref.get('identifier') for ref in refs}
    for other_alert in all_alerts:
        other_id = other_alert.get('properties', {}).get('id')
        if other_id in ref_ids:
            orig_code = extract_same_code(other_alert.get('properties', {}))
            if orig_code:
                return orig_code
    return None

def get_description_from_code(code, eas_descriptions, properties):
    first_code = code.split(',')[0].strip()
    if first_code in eas_descriptions:
        return eas_descriptions[first_code]
    else:
        headline = properties.get('headline', '')
        if ' issued ' in headline:
            description = headline.split(' issued ')[0]
        elif ' remains in effect' in headline:
            description = headline.split(' remains in effect')[0]
        elif ' in effect' in headline:
            description = headline.split(' in effect')[0]
        else:
            description = properties.get('event', 'No description available.')
        return description

def handle_no_alerts():
    alerts_file = os.path.join(SCRIPT_DIR, 'wx_alerts')
    previous_file = os.path.join(SCRIPT_DIR, 'wx_alerts_previous')
    if os.path.exists(alerts_file):
        try:
            if os.path.exists(previous_file):
                os.remove(previous_file)
            os.rename(alerts_file, previous_file)
            print("Previous alerts archived to wx_alerts_previous")
        except Exception as e:
            print(f"Warning: Could not rename wx_alerts file: {e}")

def load_same_codes():
    codes = set()
    try:
        with open(SAME_CSV, 'r') as f:
            for line in f:
                lstripped = line.lstrip().lower()
                if not line.strip() or lstripped.startswith("eas event") or ',' not in line:
                    continue
                parts = line.strip().split(',')
                if len(parts) >= 2:
                    code = parts[1].strip().upper()
                    if len(code) == 3 and code.isalpha():
                        codes.add(code)
    except Exception as e:
        print(f"Warning: Error reading same.csv for codes: {e}")
    return codes

def get_eas_only_option():
    config = configparser.ConfigParser()
    config.read(CONFIG_FILE)
    try:
        eas_only = config.getboolean('SAME Alerts', 'EAS_Only', fallback=False)
    except Exception:
        eas_only = False
    return eas_only

EAS_ONLY = get_eas_only_option()
SAME_CODES = load_same_codes() if EAS_ONLY else set()

def same_worker(same_cfg):
    zip_code_field = same_cfg["zip_code"]
    polling_time = same_cfg["polling_time"]
    user_agent = same_cfg["user_agent"]
    eas_descriptions = load_eas_descriptions()
    zone_or_zips = [z.strip() for z in zip_code_field.split(",") if z.strip()]
    while True:
        deduped_alerts = {}
        all_output_lines = []
        zones_display_labels = []
        for zone_or_zip in zone_or_zips:
            if zone_or_zip.isdigit() and len(zone_or_zip) == 5:
                zone, error_msg = get_nws_zone_from_zip(zone_or_zip, user_agent)
                display_label = f"ZIP {zone_or_zip}"
                if error_msg:
                    print(f"[SAME] Error: {error_msg}")
                    continue
            elif len(zone_or_zip) == 6 and re.match(r"^[A-Z]{2}[A-Z0-9]{4}$", zone_or_zip.upper()):
                zone = zone_or_zip.upper()
                display_label = f"Zone {zone}"
            else:
                print(f"[SAME] Invalid zone or ZIP code: {zone_or_zip}. Skipping SAME monitoring.")
                continue
            zones_display_labels.append(display_label)
            alerts_url = f"https://api.weather.gov/alerts/active?zone={zone}"
            headers = {'User-Agent': user_agent}
            try:
                with urlopen(Request(alerts_url, headers=headers), timeout=10) as response:
                    alerts_data = json.load(response)
                active_alerts = alerts_data.get('features', [])
                current_time = time.strftime('%Y-%m-%d %H:%M:%S')
                if not active_alerts:
                    print(f"[{current_time}] No active alerts for {display_label}.")
                    continue
                for alert in active_alerts:
                    alert_id = alert.get('id') or alert.get('properties', {}).get('id')
                    if not alert_id:
                        continue
                    properties = alert.get('properties', {})
                    eas_code = extract_same_code(properties)
                    event = properties.get('event', 'N/A')
                    first_eas_code = eas_code.split(',')[0].strip() if eas_code else ''
                    if EAS_ONLY and (not first_eas_code or first_eas_code not in SAME_CODES):
                        continue
                    if eas_code == 'SVS' and event == 'Severe Thunderstorm Warning':
                        continue
                    description = get_description_from_code(eas_code, eas_descriptions, properties)
                    same = eas_code
                    headline = properties.get('headline', 'N/A')
                    status = properties.get('status', 'N/A')
                    severity = properties.get('severity', 'N/A')
                    message_type = properties.get('messageType', 'N/A')
                    nws_headline = properties.get('parameters', {}).get('NWSheadline', ['N/A'])
                    if isinstance(nws_headline, list):
                        nws_headline = nws_headline[0]
                    onset = parse_nws_date(properties.get('onset'))
                    effective = parse_nws_date(properties.get('effective'))
                    ends = parse_nws_date(properties.get('ends'))
                    expires = parse_nws_date(properties.get('expires'))
                    location = properties.get('areaDesc', 'N/A')
                    nws_description = properties.get('description', 'N/A')
                    alert_text = f"""
  Event:          {event}
  SAME:           {same}
  EAS Code:       {same}
  Headline:       {headline}
  NWSheadline:    {nws_headline}
  Status:         {status}
  Severity:       {severity}
  MessageType:    {message_type}
  Onset:          {onset}
  Effective:      {effective}
  Ends:           {ends}
  Expires:        {expires}
  Location:       {location}
  Description:    {description}
"""
                    shown_fields = {
                        'event', 'same', 'eas code', 'headline', 'nwsheadline', 'status', 'severity',
                        'messagetype', 'onset', 'effective', 'ends', 'expires', 'location', 'description'
                    }
                    mapped_fields = {'areaDesc', 'Event', 'Headline', 'NWSheadline', 'Status', 'Severity',
                                     'MessageType', 'Onset', 'Effective', 'Ends', 'Expires', 'Location', 'Description'}
                    extra_lines = []
                    for key, value in properties.items():
                        key_label = key
                        if key == "areaDesc":
                            key_label = "Location"
                        if key_label.lower() in shown_fields or key in mapped_fields:
                            continue
                        if isinstance(value, list):
                            value = "; ".join(str(v) for v in value)
                        extra_lines.append(f"  {key_label}:       {value}")
                    if extra_lines:
                        alert_text += "\n" + "\n".join(extra_lines)
                    deduped_alerts[alert_id] = alert_text
            except (HTTPError, URLError) as e:
                print(f"An error occurred while checking for alerts: {e}")
        alerts_file = os.path.join(SCRIPT_DIR, 'wx_alerts')
        if deduped_alerts:
            current_time = time.strftime('%Y-%m-%d %H:%M:%S')
            output_lines = []
            output_lines.append(f"\n--- Active Alerts for {', '.join(zones_display_labels)} at {current_time} ---")
            for alert_text in deduped_alerts.values():
                output_lines.append(alert_text)
            output_lines.append("-" * 50)
            try:
                with open(alerts_file, 'w') as f:
                    f.write('\n'.join(output_lines) + '\n')
                os.chmod(alerts_file, 0o666)
                print(f"[SAME] Wrote {len(deduped_alerts)} deduped alert(s) to wx_alerts.")
            except Exception as e:
                print(f"Warning: Could not write to wx_alerts file: {e}")
        else:
            handle_no_alerts()
        time.sleep(polling_time)

# ---- WX ALERT MONITOR WITH DEDUPLICATION BY NWS ID ----

announced_alert_ids = set()

def get_nws_id_from_block(block):
    if block:
        m = re.search(r"^\s*id:\s*(urn:oid:[^\s]+)", block, re.MULTILINE)
        if m:
            return m.group(1).strip()
    return None

def get_ugc_zones_from_block(block):
    ugc_zones = []
    if block:
        m = re.search(r"geocode:\s+\{[^\}]*'UGC':\s*\[([^\]]+)\]", block)
        if m:
            ugc_zones = [z.strip().strip("'") for z in m.group(1).split(",")]
        else:
            m2 = re.search(r"affectedZones:\s+([^\n]+)", block)
            if m2:
                ugc_zones = [z.strip().split('/')[-1] for z in m2.group(1).split(';') if z.strip()]
    return ugc_zones

def log_recent(msg):
    print(f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}: {msg}")

def speak_wx_alerts_single(alert, debug_log=None):
    print(f"ANNOUNCE ALERT: {alert.get('description', '')}")

def wx_alert_monitor(current_alerts, now_dt):
    global announced_alert_ids
    for a in current_alerts:
        code = a.get("code", "")
        desc = a.get("description", "")
        expires_time = a.get("expires_time")
        block = a.get("block", "")
        nws_id = get_nws_id_from_block(block)
        ugc_zones = get_ugc_zones_from_block(block)
        zones_str = ",".join(ugc_zones) if ugc_zones else "?"

        if isinstance(expires_time, datetime):
            expires_str = expires_time.strftime('%Y-%m-%d %H:%M:%S')
            minutes_left = int((expires_time - now_dt).total_seconds() // 60)
        else:
            expires_str = str(expires_time)
            minutes_left = "?"

        log_recent(
            f"WX Alert: SAME={code} Desc='{desc}' Expires={expires_str} MinutesUntilExpire={minutes_left} NWS_ID={nws_id} NWS_Zones={zones_str}"
        )

    new_alerts = [
        a for a in current_alerts
        if get_nws_id_from_block(a.get("block", "")) and get_nws_id_from_block(a.get("block", "")) not in announced_alert_ids
    ]

    for alert in new_alerts:
        speak_wx_alerts_single(alert)
        nws_id = get_nws_id_from_block(alert.get("block", ""))
        if nws_id:
            announced_alert_ids.add(nws_id)

# ---- MAIN ----

def main():
    cfg = load_config()
    wx_cfg = cfg["wx"]
    same_cfg = cfg["same"]
    print("[drx_wx] Starting weather and SAME polling threads.")
    wx_thread = threading.Thread(target=weather_worker, args=(wx_cfg,), daemon=True)
    same_thread = threading.Thread(target=same_worker, args=(same_cfg,), daemon=True)
    wx_thread.start()
    same_thread.start()

    # If you want to use wx_alert_monitor, here's an example usage:
    # You must provide current_alerts (list of dicts) and now_dt (datetime)
    # Example (replace with real alert source!):
    # while True:
    #     current_alerts = get_current_alerts_somehow()
    #     now_dt = datetime.now()
    #     wx_alert_monitor(current_alerts, now_dt)
    #     time.sleep(60)

    try:
        while True:
            time.sleep(60)
    except KeyboardInterrupt:
        print("\n[drx_wx] Monitoring stopped by user.")

if __name__ == "__main__":
    main()