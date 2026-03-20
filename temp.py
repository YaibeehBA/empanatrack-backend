
import json
with open('firebase_credentials.json') as f:
    data = json.load(f)
print(json.dumps(data))
