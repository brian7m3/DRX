import requests
import json
import re
from datetime import datetime
import time

def get_coordinates_from_zip(zip_code):
    """
    Get latitude and longitude coordinates for a US ZIP code using the Nominatim API.
    
    Args:
        zip_code (str): US ZIP code
    
    Returns:
        tuple: (latitude, longitude) or None if not found
    """
    try:
        # Using the Nominatim API (OpenStreetMap data) - no API key required
        url = f"https://nominatim.openstreetmap.org/search?postalcode={zip_code}&country=USA&format=json"
        
        headers = {
            "User-Agent": "WeatherWarningsApp/1.0 (local-script)"
        }
        
        print(f"Getting coordinates from ZIP code: {zip_code}")
        response = requests.get(url, headers=headers)
        response.raise_for_status()
        
        data = response.json()
        
        # Check if we have results
        if data and len(data) > 0:
            lat = float(data[0]['lat'])
            lon = float(data[0]['lon'])
            location_name = data[0].get('display_name', '').split(',')[0]
            return (lat, lon, location_name)
        else:
            print(f"Could not find coordinates for ZIP code {zip_code}.")
            return None
            
    except requests.exceptions.RequestException as e:
        print(f"Error in geocoding request: {e}")
        return None
    except Exception as e:
        print(f"Unexpected error during geocoding: {e}")
        return None

def get_weather_alerts(zip_code):
    """
    Get weather alerts for a given US ZIP code using the National Weather Service API.
    
    Args:
        zip_code (str): US ZIP code
    
    Returns:
        list: List of alert dictionaries containing event, headline, and description
        str: Location name (city)
    """
    print(f"Fetching weather alerts for ZIP code: {zip_code}...")
    
    # First get coordinates for the ZIP code
    result = get_coordinates_from_zip(zip_code)
    
    if not result:
        return [], "Unknown Location"
    
    lat, lon, location_name = result
    print(f"Coordinates found for {location_name}: Lat {lat}, Lon {lon}")
    
    # Add a small delay to avoid rate limiting issues
    time.sleep(1)
    
    # Now use the coordinates to get alerts from NWS API
    try:
        # NWS API requires a User-Agent header
        headers = {
            "User-Agent": "WeatherWarningsApp/1.0 (local-script)",
            "Accept": "application/geo+json"
        }
        
        # Get the alerts for the point
        alerts_url = f"https://api.weather.gov/alerts/active?point={lat},{lon}"
        print(f"Checking for active alerts...")
        
        alerts_response = requests.get(alerts_url, headers=headers)
        alerts_response.raise_for_status()
        
        alerts_data = alerts_response.json()
        
        # Extract the relevant fields from each alert
        alerts = []
        if 'features' in alerts_data and alerts_data['features']:
            for alert in alerts_data['features']:
                properties = alert['properties']
                alerts.append({
                    'event': properties.get('event', 'No event data'),
                    'headline': properties.get('headline', 'No headline data'),
                    'description': properties.get('description', 'No description data')
                })
            print(f"Found {len(alerts)} active alerts.")
        else:
            print("No active alerts found for this location.")
            
        return alerts, location_name
        
    except requests.exceptions.HTTPError as e:
        print(f"HTTP Error when fetching alerts: {e}")
        
        # For debugging purposes
        if hasattr(e, 'response'):
            print(f"Status code: {e.response.status_code}")
            try:
                error_json = e.response.json()
                print(f"Error details: {json.dumps(error_json, indent=2)}")
            except:
                print(f"Response text: {e.response.text[:200]}")
                
        return [], location_name
    except requests.exceptions.RequestException as e:
        print(f"Request Error when fetching alerts: {e}")
        return [], location_name
    except Exception as e:
        print(f"Unexpected error when fetching alerts: {e}")
        return [], location_name

def display_alerts(alerts, location_name):
    """
    Display the alerts in a formatted way.
    
    Args:
        alerts (list): List of alert dictionaries
        location_name (str): Name of the location
    """
    print(f"\nWeather Alert Status for {location_name}")
    print("=" * 60)
    
    if not alerts:
        print("\n⚠️ NO ACTIVE WEATHER ALERTS FOR THIS LOCATION ⚠️")
        return
    
    print(f"\n📢 {len(alerts)} ACTIVE WEATHER ALERTS 📢")
    
    for i, alert in enumerate(alerts, 1):
        print(f"\nALERT #{i}:")
        print(f"EVENT: {alert['event']}")
        print(f"HEADLINE: {alert['headline']}")
        
        # Format description for better readability
        description = alert['description']
        # Truncate if too long
        if len(description) > 200:
            description = description[:200] + "..."
        
        # Replace multiple whitespaces with single space
        description = re.sub(r'\s+', ' ', description).strip()
        
        print(f"DESCRIPTION: {description}")
        print("-" * 60)

def main():
    """Main function to get user input and display results."""
    print("🌦️ Weather Warnings App 🌦️")
    print("Current Date and Time (UTC):", datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"))
    
    while True:
        zip_code = input("\nEnter a ZIP code (or 'q' to quit): ")
        
        if zip_code.lower() == 'q':
            print("Exiting application. Stay safe!")
            break
        
        # Simple validation for ZIP code format
        if not (zip_code.isdigit() and len(zip_code) == 5):
            print("Invalid ZIP code format. Please enter a 5-digit ZIP code.")
            continue
            
        alerts, location_name = get_weather_alerts(zip_code)
        display_alerts(alerts, location_name)

if __name__ == "__main__":
    main()