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
    # On your laptop, we use the local file (which is in .gitignore)
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
    if event.data is None or time.time() - start_time < 5: 
        return # Skip old data on startup

    # Example: /users/UID/devices/DEV01/history/TIMESTAMP
    path_parts = event.path.strip('/').split('/')
    if len(path_parts) < 5: return
    
    uid = path_parts[0]
    data = event.data
    
    # Logic: Get limits from Firestore and compare
    user_ref = fs.collection('users').document(uid).get()
    if user_ref.exists:
        limits = user_ref.to_dict()
        daily_limit = float(limits.get('dailylimit', 100))
        current_usage = float(data.get('energy', 0))
        
        if current_usage >= daily_limit:
            print(f"ALERT: User {uid} reached limit!")

# Start the Listener
db.reference('users').listen(handle_data)
print("Service running...")
while True: time.sleep(1)