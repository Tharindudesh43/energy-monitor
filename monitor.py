import firebase_admin
from firebase_admin import credentials, db, firestore
import time
import os
import json


firebase_config = os.environ.get('FIREBASE_CONFIG')

# Initialize Firebase
if firebase_config:
    # On the server (Heroku/VPS), we read from the environment variable
    cred_dict = json.loads(firebase_config)
    cred = credentials.Certificate(cred_dict)
else:
    try:
        cred = credentials.Certificate("serviceAccountKey.json")
    except Exception as e:
        print("Error: serviceAccountKey.json not found and FIREBASE_CONFIG not set.")
        exit(1)

firebase_admin.initialize_app(cred, {
    'databaseURL': 'https://energymonitorapp-325e9-default-rtdb.firebaseio.com/' 
})

fs = firestore.client()
start_time = time.time()

def handle_data(event):
    # 1. Log that an event was received
    print(f"\n[🕒 {time.ctime()}] 📥 Data Change Detected at: {event.path}")

    # 2. Skip initial data sync or empty data
    if event.data is None:
        print(" -> Data is empty, skipping.")
        return
        
    if time.time() - start_time < 5: 
        print(" -> Initializing: Skipping old historical data.")
        return 

    # 3. Parse Path
    path_parts = event.path.strip('/').split('/')
    if len(path_parts) < 5: 
        print(f" -> Path too short ({len(path_parts)} levels). Expected 5.")
        return
    
    uid = path_parts[0]
    device_id = path_parts[2]
    data = event.data
    
    print(f" -> Processing User: {uid} | Device: {device_id}")
    
    # 4. Fetch Limits from Firestore
    print(f" -> Fetching limits from Firestore for {uid}...")
    user_ref = fs.collection('users').document(uid).get()
    
    if user_ref.exists:
        limits = user_ref.to_dict()
        daily_limit = float(limits.get('dailylimit', 0))
        current_usage = float(data.get('energy', 0))
        
        print(f" -> Current Energy: {current_usage} kWh | Daily Limit: {daily_limit} kWh")
        
        # 5. Trigger Check
        if current_usage >= daily_limit:
            print(f"🔥 TRIGGER: Limit reached! Sending notification for {uid}...")
            # Your notification code goes here
        else:
            print(" ✅ Usage within limits. No action taken.")
    else:
        print(f" ❌ Error: User document {uid} not found in Firestore.")

def debug_listener(event):
    print(f"--- DEBUG EVENT ---")
    print(f"Path: {event.path}")
    print(f"Data: {event.data}")
    print(f"-------------------")

# Start the Listener
#db.reference('/').listen(debug_listener)
db.reference('users').listen(handle_data)
print("🚀 Energy Monitor Service is running... Waiting for data.")