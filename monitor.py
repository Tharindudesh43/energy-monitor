import firebase_admin
from firebase_admin import credentials, messaging
import json, os, time

firebase_config = os.environ.get('FIREBASE_CONFIG')
if firebase_config:
    cred = credentials.Certificate(json.loads(firebase_config))
else:
    cred = credentials.Certificate("serviceAccountKey.json")

firebase_admin.initialize_app(cred)

FCM_TOKEN = "eFyhZk5rQtaRt5m8TZMUrz:APA91bGsBrHulN7fGgqzBDg3J3ifbugGK1O_PWI-RtUtaDJHUFS2d3eLFbL6fh2c6I8TiJghYzFWxF-IRZdXF8szC33VfD1fTuGtn2zg1QPcpFYBB1vcFMU"

count = 1
while True:
    message = messaging.Message(
        notification=messaging.Notification(
            title=f"⚡ Test #{count}",
            body=f"Notification {count} sent at {time.strftime('%H:%M:%S')}",
        ),
        token=FCM_TOKEN,
    )
    response = messaging.send(message)
    print(f"✅ Sent #{count}: {response}")
    count += 1
    time.sleep(5)