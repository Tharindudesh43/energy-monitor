import firebase_admin
from firebase_admin import credentials, messaging
import json, os, time

firebase_config = os.environ.get('FIREBASE_CONFIG')
if firebase_config:
    cred = credentials.Certificate(json.loads(firebase_config))
else:
    cred = credentials.Certificate("serviceAccountKey.json")

firebase_admin.initialize_app(cred)

FCM_TOKEN = "eFtxSA0TQNmKQzwVg92YYb:APA91bHw3UrlRuKO93m1vAfX_C9pXj4gRn85siTLxwTEfrQ5DXCeuqv7d0DA5N3pP3lz3VCIoe7AGm_IPbWF4oat7-UmUNjIqkllKphpFmCSkkW8SnKfHRY"

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