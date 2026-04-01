import firebase_admin
from firebase_admin import credentials, db, firestore, messaging
import time
import os
import json
from datetime import datetime, time as dt_time
import threading
import schedule

# ── Target UID (only process data for this user) ────────────────────────────
TARGET_UID = "gGs1eueUTihcv6j44ZingWHS1QQ2"

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

last_notification_time = {}
NOTIFICATION_COOLDOWN = 3600  # 1 hour

# ── Device last-seen timestamps for hours_used calculation ──────────────────
device_last_seen = {}  # { "uid/device_id": last_epoch_seconds }


class PowerMonitor:
    def __init__(self):
        self.peak_hours_start = dt_time(18, 0)   # 6:00 PM
        self.peak_hours_end   = dt_time(22, 0)   # 10:00 PM

    def is_peak_hour(self):
        current_time = datetime.now().time()
        return self.peak_hours_start <= current_time <= self.peak_hours_end

    # ── Sri Lanka CEB tiered tariff (LKR/kWh) ──────────────────────────────
    # Domestic tariff blocks (as of 2024 CEB schedule)
    # Tier 1 :   0 –  30 kWh → LKR  2.50
    # Tier 2 :  31 –  60 kWh → LKR  4.85
    # Tier 3 :  61 –  90 kWh → LKR  7.85
    # Tier 4 :  91 – 120 kWh → LKR 10.00
    # Tier 5 : 121 – 180 kWh → LKR 27.75
    # Tier 6 : 181+          → LKR 32.00
    # Peak-hour surcharge applied on top (approx 20 % mark-up)
    CEB_TIERS = [
        (30,   2.50),
        (60,   4.85),
        (90,   7.85),
        (120, 10.00),
        (180, 27.75),
        (float('inf'), 32.00),
    ]
    PEAK_SURCHARGE = 1.20  # +20 % during peak hours

    def calculate_cost_lkr(self, watt_usage: float, hours_used: float, is_peak: bool):
        """
        Return (kwh_used, cost_lkr, effective_rate_lkr, rate_label).
        Uses CEB tiered tariff with a peak-hour surcharge.
        NOTE: For a short reading window, kwh_used will be small — the tier
        is chosen based on that incremental block alone.  In a production
        system you would pass in the monthly cumulative kWh so the correct
        tier is selected; that can be added when monthly totals are tracked.
        """
        kwh_used = (watt_usage * hours_used) / 1000.0
        remaining = kwh_used
        cost = 0.0
        prev_limit = 0.0
        effective_rate = self.CEB_TIERS[0][1]  # fallback

        for limit, rate in self.CEB_TIERS:
            block = limit - prev_limit
            if remaining <= 0:
                break
            used_in_block = min(remaining, block)
            cost += used_in_block * rate
            effective_rate = rate
            remaining -= used_in_block
            prev_limit = limit

        if is_peak:
            cost *= self.PEAK_SURCHARGE
            effective_rate *= self.PEAK_SURCHARGE
            rate_label = f"peak (×{self.PEAK_SURCHARGE})"
        else:
            rate_label = "off-peak"

        return kwh_used, cost, effective_rate, rate_label

    def get_hours_used(self, uid: str, device_id: str) -> float:
        """
        Calculate hours since last reading for this device.
        Defaults to 0.25 h (15 min) on first sight instead of a full hour.
        """
        key = f"{uid}/{device_id}"
        now = time.time()
        last = device_last_seen.get(key)
        device_last_seen[key] = now

        if last is None:
            return 0.25  # first reading — assume 15-minute window
        elapsed_hours = (now - last) / 3600.0
        # Cap at 2 h to avoid absurd cost estimates after long gaps
        return min(elapsed_hours, 2.0)

    def send_notification(self, uid, device_id, notification_type,
                          value, limit, hours_used=None):
        try:
            user_ref = fs.collection('users').document(uid).get()
            if not user_ref.exists:
                print(f"User {uid} not found")
                return False

            user_data  = user_ref.to_dict()
            fcm_token  = user_data.get('fcm_token')
            if not fcm_token:
                print(f"No FCM token for user {uid}")
                return False

            is_peak = self.is_peak_hour()

            if notification_type == "peak_limit":
                kwh, cost, rate, rate_label = self.calculate_cost_lkr(
                    value, hours_used or 0.25, True)
                title = "⚠️ Peak Hour Limit Exceeded!"
                body  = (f"Device {device_id} is drawing {value:.1f} W during peak hours "
                         f"(6 – 10 PM). Limit: {limit:.1f} W. "
                         f"Est. cost: Rs {cost:.2f} for {kwh:.3f} kWh "
                         f"at {rate_label} rate (Rs {rate:.2f}/kWh).")

            elif notification_type == "normal_limit":
                kwh, cost, rate, rate_label = self.calculate_cost_lkr(
                    value, hours_used or 0.25, False)
                title = "⚠️ Power Limit Exceeded!"
                body  = (f"Device {device_id} is drawing {value:.1f} W. "
                         f"Limit: {limit:.1f} W. "
                         f"Est. cost: Rs {cost:.2f} for {kwh:.3f} kWh "
                         f"at {rate_label} rate (Rs {rate:.2f}/kWh).")

            elif notification_type == "daily_summary":
                title = "📊 Daily Energy Summary"
                body  = f"Total energy today: {value:.3f} kWh."
                if limit > 0:
                    body += f" Daily limit: {limit:.3f} kWh."
                body += " Open the app for details."

            else:
                title = "⚡ Power Alert"
                body  = f"Device {device_id} drawing {value:.1f} W. Limit: {limit:.1f} W."

            message = messaging.Message(
                notification=messaging.Notification(title=title, body=body),
                data={
                    'type':       notification_type,
                    'device_id':  device_id,
                    'value':      str(value),
                    'limit':      str(limit),
                    'timestamp':  str(time.time()),
                },
                token=fcm_token,
            )
            response = messaging.send(message)
            print(f"✅ Notification sent to {uid}: {response}")
            return True

        except Exception as e:
            print(f"❌ Error sending notification to {uid}: {e}")
            return False

    def check_cooldown(self, uid: str) -> bool:
        last    = last_notification_time.get(uid, 0)
        elapsed = time.time() - last
        if elapsed < NOTIFICATION_COOLDOWN:
            print(f"Cooldown active for {uid}. Next in "
                  f"{NOTIFICATION_COOLDOWN - elapsed:.0f}s.")
            return False
        last_notification_time[uid] = time.time()
        return True

    def process_power_data(self, uid: str, device_id: str, data: dict):
        current_watt = float(data.get('watt', 0))
        hours_used   = self.get_hours_used(uid, device_id)   # ← calculated, not hardcoded
        is_peak      = self.is_peak_hour()

        # Fetch user limits from Firestore
        user_doc = fs.collection('users').document(uid).get()
        if not user_doc.exists:
            print(f"❌ User {uid} not found in Firestore")
            return

        user_limits  = user_doc.to_dict()
        normal_limit = float(user_limits.get('NormalWattLimit', 0))
        peak_limit   = float(user_limits.get('PeakWattLimit', 0))

        kwh, cost, rate, rate_label = self.calculate_cost_lkr(
            current_watt, hours_used, is_peak)

        print(f"\n--- Power Check [{datetime.now().strftime('%H:%M:%S')}] "
              f"uid={uid} device={device_id} ---")
        print(f"  Wattage   : {current_watt:.2f} W")
        print(f"  Period    : {'PEAK HOUR ⚡' if is_peak else 'Off-peak'}")
        print(f"  Window    : {hours_used * 60:.1f} min  →  {kwh:.4f} kWh")
        print(f"  Cost (est): Rs {cost:.2f}  @ {rate_label} (Rs {rate:.2f}/kWh)")
        print(f"  Limits    : normal={normal_limit:.1f} W  peak={peak_limit:.1f} W")

        self.store_usage_history(uid, device_id, current_watt, kwh, cost, is_peak)

        if not self.check_cooldown(uid):
            return

        if is_peak and peak_limit > 0:
            if current_watt >= peak_limit:
                print(f"🔥 PEAK LIMIT EXCEEDED: {current_watt:.1f} W > {peak_limit:.1f} W")
                self.send_notification(uid, device_id, "peak_limit",
                                       current_watt, peak_limit, hours_used)
            else:
                print(f"✅ Within peak limit ({current_watt:.1f}/{peak_limit:.1f} W)")

        elif not is_peak and normal_limit > 0:
            if current_watt >= normal_limit:
                print(f"🔥 NORMAL LIMIT EXCEEDED: {current_watt:.1f} W > {normal_limit:.1f} W")
                self.send_notification(uid, device_id, "normal_limit",
                                       current_watt, normal_limit, hours_used)
            else:
                print(f"✅ Within normal limit ({current_watt:.1f}/{normal_limit:.1f} W)")

    def store_usage_history(self, uid, device_id, watt, kwh, cost, is_peak):
        try:
            fs.collection('users').document(uid).collection('usage_history').add({
                'timestamp':   firestore.SERVER_TIMESTAMP,
                'device_id':   device_id,
                'watt':        watt,
                'kwh':         kwh,
                'cost_lkr':    cost,
                'is_peak_hour': is_peak,
                'date':        datetime.now().strftime('%Y-%m-%d'),
                'hour':        datetime.now().hour,
            })
            print(f"📊 Usage history stored for {uid}")
        except Exception as e:
            print(f"Error storing usage history: {e}")

    def send_daily_summary(self):
        print("\n📊 Generating daily summaries...")
        try:
            today = datetime.now().strftime('%Y-%m-%d')
            users = fs.collection('users').stream()

            for user in users:
                uid        = user.id
                user_data  = user.to_dict()
                daily_limit = float(user_data.get('dailylimit', 0))

                usage_query = (fs.collection('users').document(uid)
                               .collection('usage_history')
                               .where('date', '==', today).stream())

                total_kwh  = sum(u.to_dict().get('kwh', 0)      for u in usage_query)

                # Re-query (stream is exhausted after one iteration)
                usage_query = (fs.collection('users').document(uid)
                               .collection('usage_history')
                               .where('date', '==', today).stream())
                total_cost = sum(u.to_dict().get('cost_lkr', 0) for u in usage_query)

                if total_kwh > 0 and user_data.get('fcm_token'):
                    print(f"  {uid}: {total_kwh:.3f} kWh  Rs {total_cost:.2f}")
                    self.send_notification(uid, "", "daily_summary",
                                           total_kwh, daily_limit)
        except Exception as e:
            print(f"Error sending daily summaries: {e}")


# ── Instantiate monitor ──────────────────────────────────────────────────────
monitor = PowerMonitor()


def handle_data(event):
    """Real-time Database event handler — filtered to TARGET_UID."""
    if event.data is None:
        return
    if time.time() - start_time < 5:
        print("Init: skipping historical data.")
        return

    # Expected RTDB path structure:  /users/{uid}/devices/{device_id}/...
    # event.path is relative to the listener root ('users'), so it looks like:
    #   /{uid}/devices/{device_id}   OR   /{uid}/devices/{device_id}/watt
    parts = event.path.strip('/').split('/')
    # parts[0] = uid, parts[1] = 'devices', parts[2] = device_id, ...

    if len(parts) < 3:
        print(f"Path too short ({len(parts)} parts): {event.path}")
        return

    uid       = parts[0]
    devices_key = parts[1]   # should be 'devices'
    device_id = parts[2]

    # ── Filter: only process the target user ──
    if uid != TARGET_UID:
        return

    if devices_key != 'devices':
        return

    # event.data may be the full device dict or a single field update
    data = event.data if isinstance(event.data, dict) else {}
    if not isinstance(event.data, dict):
        # Single-field update (e.g. path = /uid/devices/dev1/watt)
        # Reconstruct a minimal dict using the last path segment as key
        field = parts[-1] if len(parts) > 3 else 'watt'
        data  = {field: event.data}

    print(f"\n[🕒 {time.ctime()}] Event for {uid}/{device_id}: {data}")
    monitor.process_power_data(uid, device_id, data)


def schedule_daily_summary():
    schedule.every().day.at("23:59").do(monitor.send_daily_summary)
    while True:
        schedule.run_pending()
        time.sleep(60)


# ── Start ────────────────────────────────────────────────────────────────────
db.reference('users').listen(handle_data)

summary_thread = threading.Thread(target=schedule_daily_summary, daemon=True)
summary_thread.start()

print("🚀 Energy Monitor running")
print(f"   Target UID  : {TARGET_UID}")
print(f"   Peak hours  : 06:00 PM – 10:00 PM")
print(f"   Tariff      : CEB tiered (LKR), peak surcharge ×{PowerMonitor.PEAK_SURCHARGE}")
print("   Waiting for data...\n")

try:
    while True:
        time.sleep(1)
except KeyboardInterrupt:
    print("\n👋 Shutting down Energy Monitor...")