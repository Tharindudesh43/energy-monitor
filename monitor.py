import firebase_admin
from firebase_admin import credentials, db, firestore, messaging
import time
import os
import json
from datetime import datetime

# Initialize Firebase
firebase_config = os.environ.get('FIREBASE_CONFIG')
if firebase_config:
    cred_dict = json.loads(firebase_config)
    cred = credentials.Certificate(cred_dict)
else:
    cred = credentials.Certificate("serviceAccountKey.json")

firebase_admin.initialize_app(cred, {
    'databaseURL': 'https://energymonitorapp-325e9-default-rtdb.firebaseio.com/'
})

fs = firestore.client()
start_time = time.time()

def is_peak_time():
    """Check if current time is between 6 PM and 10 PM"""
    current_hour = datetime.now().hour
    return 18 <= current_hour < 22

def send_notification(fcm_token, title, body):
    """Send push notification to user"""
    if not fcm_token:
        return False
    
    message = messaging.Message(
        notification=messaging.Notification(
            title=title,
            body=body,
        ),
        token=fcm_token,
    )
    
    try:
        response = messaging.send(message)
        print(f"   ✅ Notification sent: {response}")
        return True
    except Exception as e:
        print(f"   ❌ Failed to send notification: {e}")
        return False

def check_and_notify(uid, power_watts, device_id):
    """Check limits and send notification if exceeded"""
    try:
        # Get user document from Firestore
        user_doc = fs.collection('users').document(uid).get()
        
        if not user_doc.exists:
            print(f"   ❌ User {uid} not found in Firestore")
            return
        
        user_data = user_doc.to_dict()
        fcm_token = user_data.get('fcmToken')
        
        if not fcm_token:
            print(f"   ⚠️ No FCM token for user {uid}")
            return
        
        # Determine which limit to use
        if is_peak_time():
            limit = user_data.get('PeakWattLimit', 0)
            time_type = "Peak Hours (6PM-10PM)"
        else:
            limit = user_data.get('NormalWattLimit', 0)
            time_type = "Normal Hours"
        
        # Check if limit is exceeded
        if limit > 0 and power_watts > limit:
            print(f"   🔥 LIMIT EXCEEDED! Power: {power_watts}W | {time_type} Limit: {limit}W")
            
            title = "⚠️ Power Limit Exceeded"
            body = f"Device {device_id}: {power_watts}W exceeds {time_type} limit of {limit}W"
            
            send_notification(fcm_token, title, body)
        else:
            print(f"   ✅ Within limits: {power_watts}W / {limit}W ({time_type})")
            
    except Exception as e:
        print(f"   ❌ Error checking limits: {e}")

def handle_data(event):
    """Handle realtime database changes"""
    # Skip initial sync
    if time.time() - start_time < 5 or event.data is None:
        return
    
    # Parse path: users/{uid}/devices/{device_id}/latest
    path_parts = event.path.strip('/').split('/')
    
    # We're only interested in 'latest' data changes
    if len(path_parts) < 4 or path_parts[-1] != 'latest':
        return
    
    uid = path_parts[0]  # users/{uid}
    device_id = path_parts[2]  # devices/{device_id}
    data = event.data
    
    print(f"\n[🕒 {datetime.now().strftime('%H:%M:%S')}] User: {uid} | Device: {device_id}")
    
    # Get power value (in watts) from the latest data
    power_watts = float(data.get('power', 0))
    
    if power_watts > 0:
        print(f"   ⚡ Current Power: {power_watts}W")
        print(f"   🌡️ Temperature: {data.get('temperature', 'N/A')}°C")
        print(f"   💧 Humidity: {data.get('humidity', 'N/A')}%")
        check_and_notify(uid, power_watts, device_id)
    else:
        print(f"   ℹ️ Power reading: 0W (device may be off or pzemOK: {data.get('pzemOK', False)})")

# Start listening to 'latest' nodes
print("🚀 Power Monitor Service Started")
print(f"📅 Current time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
print(f"⏰ Peak hours: 6 PM - 10 PM")
print("-" * 50)

# Listen to all 'latest' nodes under any user and device
db.reference('users').listen(handle_data)

# Keep the script running
try:
    while True:
        time.sleep(1)
except KeyboardInterrupt:
    print("\n👋 Service stopped")