import firebase_admin
from firebase_admin import credentials, db, firestore, messaging
import time
import os
import json
from datetime import datetime
from typing import Dict, Optional, Tuple
import logging

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

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

# Configuration
NOTIFICATION_COOLDOWN_MINUTES = 20
PEAK_HOURS_START = 18  # 6 PM
PEAK_HOURS_END = 22    # 10 PM

# Store last notification time
last_notification_time: Dict[str, float] = {}

class PowerMonitor:
    """Main power monitoring service"""
    
    def __init__(self):
        self.fs = firestore.client()
        self.notification_cooldown = NOTIFICATION_COOLDOWN_MINUTES * 60
        
    def is_peak_time(self) -> bool:
        """Check if current time is during peak hours"""
        current_hour = datetime.now().hour
        return PEAK_HOURS_START <= current_hour < PEAK_HOURS_END
    
    def get_cooldown_key(self, uid: str, device_id: str, is_peak: bool) -> str:
        """Generate unique key for cooldown tracking"""
        time_type = "peak" if is_peak else "normal"
        return f"{uid}_{device_id}_{time_type}"
    
    def can_send_notification(self, uid: str, device_id: str, is_peak: bool) -> Tuple[bool, int]:
        """Check if enough time has passed since last notification"""
        cooldown_key = self.get_cooldown_key(uid, device_id, is_peak)
        current_time = time.time()
        
        if cooldown_key in last_notification_time:
            time_since_last = current_time - last_notification_time[cooldown_key]
            if time_since_last < self.notification_cooldown:
                remaining = int((self.notification_cooldown - time_since_last) / 60)
                return False, remaining
        
        return True, 0
    
    def send_notification(self, fcm_token: str, title: str, body: str, 
                         uid: str, device_id: str, is_peak: bool) -> bool:
        """Send push notification with cooldown tracking"""
        if not fcm_token:
            return False
        
        can_send, remaining = self.can_send_notification(uid, device_id, is_peak)
        if not can_send:
            logger.info(f"Cooldown active for {uid}/{device_id}: {remaining}min remaining")
            return False
        
        message = messaging.Message(
            notification=messaging.Notification(title=title, body=body),
            token=fcm_token,
            data={
                'uid': uid,
                'device_id': device_id,
                'power_watts': str(body.split(':')[1].split('W')[0].strip()),
                'timestamp': str(datetime.now().timestamp())
            }
        )
        
        try:
            response = messaging.send(message)
            cooldown_key = self.get_cooldown_key(uid, device_id, is_peak)
            last_notification_time[cooldown_key] = time.time()
            logger.info(f"✅ Notification sent to {uid}: {response}")
            
            # Store notification in Firestore for history
            self.store_notification_history(uid, device_id, body, is_peak)
            return True
        except Exception as e:
            logger.error(f"Failed to send notification: {e}")
            return False
    
    def store_notification_history(self, uid: str, device_id: str, message: str, is_peak: bool):
        """Store notification in Firestore for history"""
        try:
            notification_ref = self.fs.collection('users').document(uid)\
                .collection('notifications').document()
            
            notification_ref.set({
                'device_id': device_id,
                'message': message,
                'is_peak_hours': is_peak,
                'timestamp': datetime.now(),
                'type': 'limit_exceeded'
            })
        except Exception as e:
            logger.error(f"Failed to store notification history: {e}")
    
    def get_user_limits(self, uid: str) -> Tuple[Optional[float], Optional[float], Optional[str]]:
        """Fetch user limits and FCM token from Firestore"""
        try:
            user_doc = self.fs.collection('users').document(uid).get()
            if not user_doc.exists:
                return None, None, None
            
            user_data = user_doc.to_dict()
            normal_limit = user_data.get('NormalWattLimit')
            peak_limit = user_data.get('PeakWattLimit')
            fcm_token = user_data.get('fcm_token')
            
            return normal_limit, peak_limit, fcm_token
        except Exception as e:
            logger.error(f"Error fetching user limits: {e}")
            return None, None, None
    
    def check_limits(self, uid: str, power_watts: float, device_id: str):
        """Check limits and trigger notification if exceeded"""
        normal_limit, peak_limit, fcm_token = self.get_user_limits(uid)
        
        if not fcm_token:
            logger.warning(f"No FCM token for user {uid}")
            return
        
        is_peak = self.is_peak_time()
        current_limit = peak_limit if is_peak else normal_limit
        time_type = "Peak Hours" if is_peak else "Normal Hours"
        
        if current_limit and power_watts > current_limit:
            logger.info(f"⚠️ Limit exceeded: {power_watts}W > {current_limit}W ({time_type})")
            
            title = "⚠️ Power Limit Exceeded"
            body = f"Device {device_id}: {power_watts}W exceeds {time_type} limit of {current_limit}W"
            
            self.send_notification(fcm_token, title, body, uid, device_id, is_peak)
        elif current_limit:
            # Reset cooldown when back within limits
            cooldown_key = self.get_cooldown_key(uid, device_id, is_peak)
            if cooldown_key in last_notification_time:
                del last_notification_time[cooldown_key]
                logger.info(f"Cooldown reset for {uid}/{device_id}")
    
    def process_data(self, event):
        """Process realtime database events"""
        # Skip initial sync
        if time.time() - start_time < 5 or event.data is None:
            return
        
        # Parse path
        path_parts = event.path.strip('/').split('/')
        if len(path_parts) < 4 or path_parts[-1] != 'latest':
            return
        
        uid = path_parts[0]
        device_id = path_parts[2]
        data = event.data
        
        power_watts = float(data.get('power', 0))
        
        if power_watts > 0:
            logger.info(f"📊 {uid}/{device_id}: {power_watts}W")
            self.check_limits(uid, power_watts, device_id)

# Start monitoring
if __name__ == "__main__":
    monitor = PowerMonitor()
    logger.info("🚀 Power Monitor Service Started")
    logger.info(f"⏰ Peak hours: {PEAK_HOURS_START}:00 - {PEAK_HOURS_END}:00")
    logger.info(f"⏱️ Cooldown: {NOTIFICATION_COOLDOWN_MINUTES} minutes")
    
    db.reference('users').listen(monitor.process_data)
    
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        logger.info("👋 Service stopped")