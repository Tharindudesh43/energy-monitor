import firebase_admin
from firebase_admin import credentials, db, firestore, messaging
import time
import os
import json
from datetime import datetime, time as dt_time
import threading
import schedule

firebase_config = os.environ.get('FIREBASE_CONFIG')

# Initialize Firebase
if firebase_config:
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

# Store last notification time to avoid spamming (cooldown period)
last_notification_time = {}
NOTIFICATION_COOLDOWN = 3600  # 1 hour cooldown between notifications for same user

class PowerMonitor:
    def __init__(self):
        self.peak_hours_start = dt_time(18, 0)  # 6:00 PM
        self.peak_hours_end = dt_time(22, 0)    # 10:00 PM
        
    def is_peak_hour(self):
        """Check if current time is within peak hours"""
        current_time = datetime.now().time()
        return self.peak_hours_start <= current_time <= self.peak_hours_end
    
    def calculate_cost(self, watt_usage, hours_used, is_peak_hour):
        """
        Calculate electricity cost based on usage and time
        Assuming rates: 
        - Peak rate: $0.25 per kWh
        - Off-peak rate: $0.15 per kWh
        """
        kwh_used = (watt_usage * hours_used) / 1000
        
        if is_peak_hour:
            rate = 0.25  # Peak rate per kWh
            rate_type = "peak"
        else:
            rate = 0.15  # Off-peak rate per kWh
            rate_type = "normal"
            
        cost = kwh_used * rate
        return kwh_used, cost, rate_type
    
    def send_notification(self, uid, device_id, notification_type, watt_usage, limit, hours_used=None):
        """
        Send push notification to user's device
        """
        try:
            # Get user's FCM token from Firestore
            user_ref = fs.collection('users').document(uid).get()
            if not user_ref.exists:
                print(f"User {uid} not found")
                return False
                
            user_data = user_ref.to_dict()
            fcm_token = user_data.get('fcm_token')
            
            if not fcm_token:
                print(f"No FCM token found for user {uid}")
                return False
            
            # Prepare notification based on type
            if notification_type == "peak_limit":
                title = "⚠️ Peak Hour Limit Exceeded!"
                kwh_used, cost, rate_type = self.calculate_cost(watt_usage, hours_used, True)
                body = (f"Device {device_id} using {watt_usage:.1f}W during peak hours "
                       f"(6-10 PM). Exceeds limit of {limit:.1f}W. "
                       f"Estimated cost: ${cost:.2f} for {kwh_used:.2f} kWh "
                       f"at peak rate (${0.25:.2f}/kWh)")
                       
            elif notification_type == "normal_limit":
                title = "⚠️ Power Limit Exceeded!"
                kwh_used, cost, rate_type = self.calculate_cost(watt_usage, hours_used, False)
                body = (f"Device {device_id} using {watt_usage:.1f}W. "
                       f"Exceeds limit of {limit:.1f}W. "
                       f"Estimated cost: ${cost:.2f} for {kwh_used:.2f} kWh "
                       f"at normal rate (${0.15:.2f}/kWh)")
            
            elif notification_type == "daily_summary":
                title = "📊 Daily Energy Summary"
                body = f"Total energy usage: {watt_usage:.2f} kWh. "
                if limit > 0:
                    body += f"Daily limit: {limit:.2f} kWh. "
                body += "Check app for details."
                
            else:
                title = "⚡ Power Alert"
                body = f"Device {device_id} using {watt_usage:.1f}W. Limit: {limit:.1f}W"
            
            # Create notification message
            message = messaging.Message(
                notification=messaging.Notification(
                    title=title,
                    body=body,
                ),
                data={
                    'type': notification_type,
                    'device_id': device_id,
                    'watt_usage': str(watt_usage),
                    'limit': str(limit),
                    'timestamp': str(time.time()),
                },
                token=fcm_token,
            )
            
            # Send notification
            response = messaging.send(message)
            print(f"✅ Notification sent to {uid}: {response}")
            return True
            
        except Exception as e:
            print(f"❌ Error sending notification to {uid}: {e}")
            return False
    
    def check_cooldown(self, uid):
        """Check if we should send another notification (cooldown period)"""
        last_time = last_notification_time.get(uid, 0)
        current_time = time.time()
        
        if current_time - last_time < NOTIFICATION_COOLDOWN:
            print(f"Cooldown active for {uid}. Last notification {current_time - last_time:.0f}s ago")
            return False
        
        last_notification_time[uid] = current_time
        return True
    
    def process_power_data(self, uid, device_id, data):
        """Process power data and check limits"""
        # Get current watt usage
        current_watt = float(data.get('watt', 0))
        
        # Get user limits from Firestore
        user_ref = fs.collection('users').document(uid).get()
        if not user_ref.exists:
            print(f"❌ User {uid} not found")
            return
        
        user_limits = user_ref.to_dict()
        normal_limit = float(user_limits.get('NormalWattLimit', 0))
        peak_limit = float(user_limits.get('PeakWattLimit', 0))
        
        # Get device usage duration (in hours) - you may need to calculate this
        # For now, assume 1 hour if not provided
        hours_used = float(data.get('hours_used', 1))
        
        # Check if current time is peak hour
        is_peak = self.is_peak_hour()
        
        print(f"\n--- Power Check for {uid} ---")
        print(f"Device: {device_id}")
        print(f"Current Wattage: {current_watt:.2f}W")
        print(f"Time: {'PEAK HOUR' if is_peak else 'Normal Hour'} ({datetime.now().strftime('%H:%M')})")
        print(f"Normal Limit: {normal_limit:.2f}W")
        print(f"Peak Limit: {peak_limit:.2f}W")
        
        # Calculate cost for current usage
        kwh_used, cost, rate_type = self.calculate_cost(current_watt, hours_used, is_peak)
        print(f"Estimated cost for {hours_used}h: ${cost:.2f} ({kwh_used:.2f} kWh at {rate_type} rate)")
        
        # Check cooldown before sending notifications
        if not self.check_cooldown(uid):
            return
        
        # Check limits based on time
        if is_peak and peak_limit > 0:
            if current_watt >= peak_limit:
                print(f"🔥 PEAK HOUR LIMIT EXCEEDED! {current_watt:.2f}W > {peak_limit:.2f}W")
                self.send_notification(uid, device_id, "peak_limit", 
                                      current_watt, peak_limit, hours_used)
            else:
                print(f"✅ Within peak limit: {current_watt:.2f}W / {peak_limit:.2f}W")
                
        elif not is_peak and normal_limit > 0:
            if current_watt >= normal_limit:
                print(f"🔥 NORMAL LIMIT EXCEEDED! {current_watt:.2f}W > {normal_limit:.2f}W")
                self.send_notification(uid, device_id, "normal_limit", 
                                      current_watt, normal_limit, hours_used)
            else:
                print(f"✅ Within normal limit: {current_watt:.2f}W / {normal_limit:.2f}W")
        
        # Store usage history in Firestore for reporting
        self.store_usage_history(uid, device_id, current_watt, kwh_used, cost, is_peak)
    
    def store_usage_history(self, uid, device_id, watt, kwh, cost, is_peak):
        """Store usage data for historical analysis"""
        try:
            usage_data = {
                'timestamp': firestore.SERVER_TIMESTAMP,
                'device_id': device_id,
                'watt': watt,
                'kwh': kwh,
                'cost': cost,
                'is_peak_hour': is_peak,
                'date': datetime.now().strftime('%Y-%m-%d'),
                'hour': datetime.now().hour
            }
            
            # Store in user's subcollection
            fs.collection('users').document(uid).collection('usage_history').add(usage_data)
            print(f"📊 Usage history stored for {uid}")
            
        except Exception as e:
            print(f"Error storing usage history: {e}")
    
    def send_daily_summary(self):
        """Send daily summary to all users at midnight"""
        print("\n📊 Generating daily summaries...")
        
        try:
            users = fs.collection('users').stream()
            today = datetime.now().strftime('%Y-%m-%d')
            
            for user in users:
                uid = user.id
                user_data = user.to_dict()
                daily_limit = user_data.get('dailylimit', 0)
                
                # Get today's usage
                usage_ref = fs.collection('users').document(uid).collection('usage_history')
                today_usage = usage_ref.where('date', '==', today).stream()
                
                total_kwh = 0
                total_cost = 0
                
                for usage in today_usage:
                    usage_data = usage.to_dict()
                    total_kwh += usage_data.get('kwh', 0)
                    total_cost += usage_data.get('cost', 0)
                
                if total_kwh > 0:
                    print(f"User {uid}: Total {total_kwh:.2f} kWh, Cost ${total_cost:.2f}")
                    
                    # Send daily summary notification
                    if user_data.get('fcm_token'):
                        self.send_notification(uid, "", "daily_summary", 
                                              total_kwh, daily_limit)
                        
        except Exception as e:
            print(f"Error sending daily summaries: {e}")

# Create monitor instance
monitor = PowerMonitor()

def handle_data(event):
    """Main handler for real-time database events"""
    # Skip initial sync data
    if event.data is None:
        print("Data is empty, skipping.")
        return
        
    if time.time() - start_time < 5: 
        print("Initializing: Skipping old historical data.")
        return 

    # Parse the path: /users/{uid}/devices/{device_id}/data
    path_parts = event.path.strip('/').split('/')
    if len(path_parts) < 5: 
        print(f"Path too short ({len(path_parts)} levels). Expected at least 5.")
        return
    
    # Extract UID and device ID from path
    uid = path_parts[1]  # After 'users'
    device_id = path_parts[3]  # After 'devices'
    data = event.data
    
    print(f"\n[🕒 {time.ctime()}] Processing data for {uid}/{device_id}")
    
    # Process the power data
    monitor.process_power_data(uid, device_id, data)

def schedule_daily_summary():
    """Schedule daily summary at midnight"""
    schedule.every().day.at("23:59").do(monitor.send_daily_summary)
    
    while True:
        schedule.run_pending()
        time.sleep(60)  # Check every minute

# Start the real-time listener
db.reference('users').listen(handle_data)

# Start daily summary scheduler in background thread
summary_thread = threading.Thread(target=schedule_daily_summary, daemon=True)
summary_thread.start()

print("🚀 Energy Monitor Service is running with power limits and notifications...")
print(f"Peak Hours: 6:00 PM - 10:00 PM")
print("Waiting for data...")

# Keep the main thread alive
try:
    while True:
        time.sleep(1)
except KeyboardInterrupt:
    print("\n👋 Shutting down Energy Monitor Service...")