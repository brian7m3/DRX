import json
from urllib.request import urlopen, Request
from urllib.error import URLError, HTTPError
import time
from datetime import datetime
import os
import csv
import configparser

def load_config():
    """
    Load configuration from wx_config.ini file in the script's directory.
    Returns zip_code and polling_time, or defaults if not found.
    """
    script_dir = os.path.dirname(os.path.abspath(__file__))
    config_path = os.path.join(script_dir, 'wx_config.ini')
    
    config = configparser.ConfigParser()
    
    # Default values
    zip_code = None
    polling_time = 300  # Default 5 minutes
    
    try:
        config.read(config_path)
        
        if 'SAME Alerts' in config:
            zip_code = config.get('SAME Alerts', 'zip_code', fallback=None)
            if zip_code:
                zip_code = zip_code.strip()
            
            polling_str = config.get('SAME Alerts', 'polling_time', fallback='300')
            try:
                polling_time = int(polling_str.strip())
            except ValueError:
                print(f"Warning: Invalid polling_time '{polling_str}' in config, using default 300 seconds")
                polling_time = 300
                
    except Exception as e:
        print(f"Warning: Error reading wx_config.ini: {e}")
    
    return zip_code, polling_time

def load_eas_descriptions():
    """
    Load EAS code descriptions from same.csv file in the script's directory.
    Returns a dictionary mapping EAS codes to descriptions.
    """
    descriptions = {}
    script_dir = os.path.dirname(os.path.abspath(__file__))
    csv_path = os.path.join(script_dir, 'same.csv')
    
    try:
        with open(csv_path, 'r') as f:
            reader = csv.reader(f)
            for row in reader:
                if len(row) >= 2:
                    description = row[0].strip()
                    code = row[1].strip()
                    descriptions[code] = description
    except FileNotFoundError:
        print(f"Warning: same.csv not found in {script_dir}")
        print("Falling back to API descriptions.")
    except Exception as e:
        print(f"Warning: Error reading same.csv: {e}")
        print("Falling back to API descriptions.")
    
    return descriptions

def parse_nws_date(date_string):
    """
    Parses an ISO 8601 date string from the NWS API and formats it.
    Example input: '2024-07-16T14:58:00-05:00'
    Returns: '2024-07-16 14:58:00'
    """
    if not date_string:
        return "N/A"
    try:
        dt_obj = datetime.fromisoformat(date_string)
        return dt_obj.strftime('%Y-%m-%d %H:%M:%S')
    except (ValueError, TypeError):
        return date_string

def get_nws_zone_from_zip(zip_code, user_agent):
    """
    Finds the NWS forecast zone ID for a given US zip code.
    """
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
            zone_url = points_data.get('properties', {}).get('forecastZone')
            if zone_url:
                zone_id = zone_url.split('/')[-1]
                return zone_id, None
            else:
                return None, "Could not determine NWS forecast zone from API response."
    except (HTTPError, URLError) as e:
        return None, f"Could not contact NWS API for zone info. Reason: {e}"
    except KeyError:
        return None, "NWS API response for zone info was in an unexpected format."

def extract_same_code(properties):
    """
    Extract the 3-letter SAME code(s) from the alert properties.
    Returns multiple codes separated by commas if present.
    """
    # The eventCode field contains the raw dictionary like {'SAME': ['FFA', 'FFW'], 'NationalWeatherService': ['FAA']}
    event_code = properties.get('eventCode')
    
    all_codes = []
    
    if isinstance(event_code, dict):
        # Extract from SAME array
        same_codes = event_code.get('SAME', [])
        if same_codes and isinstance(same_codes, list):
            all_codes.extend(same_codes)
        
        # If no SAME codes, try NationalWeatherService
        if not all_codes:
            nws_codes = event_code.get('NationalWeatherService', [])
            if nws_codes and isinstance(nws_codes, list):
                all_codes.extend(nws_codes)
    
    elif isinstance(event_code, str):
        # If it's already a string, just add it
        all_codes.append(event_code)
    
    # Return comma-separated list of codes, or 'N/A' if none found
    return ', '.join(all_codes) if all_codes else 'N/A'

def get_description_from_code(code, eas_descriptions, properties):
    """
    Get description for an EAS code from the CSV file.
    Falls back to API data if code not found in CSV.
    """
    # If multiple codes, use the first one for lookup
    first_code = code.split(',')[0].strip()
    
    if first_code in eas_descriptions:
        return eas_descriptions[first_code]
    else:
        # Fall back to extracting from headline
        headline = properties.get('headline', '')
        if ' issued ' in headline:
            description = headline.split(' issued ')[0]
        elif ' remains in effect' in headline:
            description = headline.split(' remains in effect')[0]
        elif ' in effect' in headline:
            description = headline.split(' in effect')[0]
        else:
            # Fall back to event field
            description = properties.get('event', 'No description available.')
        return description

def write_alerts_to_file(alert_text):
    """
    Write alert text to wx_alerts file with 0666 permissions.
    """
    script_dir = os.path.dirname(os.path.abspath(__file__))
    alerts_file = os.path.join(script_dir, 'wx_alerts')
    
    try:
        with open(alerts_file, 'a') as f:
            f.write(alert_text)
        
        # Set permissions to 0666 (readable and writable by all)
        os.chmod(alerts_file, 0o666)
    except Exception as e:
        print(f"Warning: Could not write to wx_alerts file: {e}")

def handle_no_alerts():
    """
    When no alerts are active, rename wx_alerts to wx_alerts_previous if it exists.
    """
    script_dir = os.path.dirname(os.path.abspath(__file__))
    alerts_file = os.path.join(script_dir, 'wx_alerts')
    previous_file = os.path.join(script_dir, 'wx_alerts_previous')
    
    # Check if wx_alerts exists
    if os.path.exists(alerts_file):
        try:
            # Remove wx_alerts_previous if it exists (to overwrite)
            if os.path.exists(previous_file):
                os.remove(previous_file)
            
            # Rename wx_alerts to wx_alerts_previous
            os.rename(alerts_file, previous_file)
            print("Previous alerts archived to wx_alerts_previous")
        except Exception as e:
            print(f"Warning: Could not rename wx_alerts file: {e}")

def check_for_active_alerts(zone_id, zip_code, user_agent, eas_descriptions):
    """
    Checks the NWS API for active alerts and displays them in the requested format.
    """
    alerts_url = f"https://api.weather.gov/alerts/active?zone={zone_id}"
    headers = {'User-Agent': user_agent}
    try:
        with urlopen(Request(alerts_url, headers=headers), timeout=10) as response:
            alerts_data = json.load(response)
        
        active_alerts = alerts_data.get('features', [])
        
        current_time = time.strftime('%Y-%m-%d %H:%M:%S')
        if not active_alerts:
            print(f"[{current_time}] No active alerts for ZIP Code {zip_code} (Zone: {zone_id}).")
            # Handle the no alerts case - rename files if needed
            handle_no_alerts()
        else:
            # Build output string for both console and file
            output_lines = []
            output_lines.append(f"\n--- Active Alerts for ZIP {zip_code} at {current_time} ---")
            
            for alert in active_alerts:
                properties = alert.get('properties', {})
                
                # Extract the 3-letter SAME code(s)
                eas_code = extract_same_code(properties)
                
                # Get description from CSV file based on code
                description = get_description_from_code(eas_code, eas_descriptions, properties)
                
                # Extract additional fields
                event = properties.get('event', 'N/A')
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

                # Get the API's description field
                nws_description = properties.get('description', 'N/A')
                
                alert_text = f"""
  Event:          {event}
  SAME:           {same}
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
  NWS_Description:{nws_description}
  Monitored:      ZIP Code {zip_code}
"""
                output_lines.append(alert_text)
            
            output_lines.append("-" * 50)
            
            # Join all lines
            full_output = '\n'.join(output_lines)
            
            # Print to console
            print(full_output)
            
            # Write to file
            write_alerts_to_file(full_output + '\n')

    except (HTTPError, URLError) as e:
        print(f"An error occurred while checking for alerts: {e}")

if __name__ == "__main__":
    USER_AGENT = "WX-SAME-Script (brian7m3@example.com)"
    
    # Load configuration
    config_zip, polling_time = load_config()
    
    # Load EAS descriptions from CSV file
    eas_descriptions = load_eas_descriptions()

    # Get ZIP code - from config or ask user
    if config_zip and config_zip.isdigit() and len(config_zip) == 5:
        zip_input = config_zip
        print(f"Using ZIP code from config: {zip_input}")
    else:
        zip_input = input("Enter a 5-digit US ZIP code to monitor for alerts: ").strip()
        if not (zip_input.isdigit() and len(zip_input) == 5):
            print("Invalid ZIP code. Exiting.")
            exit()

    print(f"Polling interval: {polling_time} seconds")
    print("Finding NWS forecast zone for that ZIP code...")
    zone, error_msg = get_nws_zone_from_zip(zip_input, USER_AGENT)
    
    if error_msg:
        print(f"Error: {error_msg}")
        exit()

    print(f"Successfully found NWS Zone: {zone}. Starting monitor for ZIP {zip_input}...")
    
    try:
        while True:
            check_for_active_alerts(zone, zip_input, USER_AGENT, eas_descriptions)
            time.sleep(polling_time)
    except KeyboardInterrupt:
        print("\nMonitoring stopped by user.")