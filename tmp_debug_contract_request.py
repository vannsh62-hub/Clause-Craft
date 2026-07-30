import urllib.request
import urllib.parse
import urllib.error

url = 'http://127.0.0.1:8000/api/v1/contracts'
data = urllib.parse.urlencode({'request': 'Test contract'}).encode()
req = urllib.request.Request(url, data=data, method='POST')
req.add_header('Content-Type', 'application/x-www-form-urlencoded')
try:
    with urllib.request.urlopen(req) as resp:
        print(resp.status)
        print(resp.read().decode())
except urllib.error.HTTPError as exc:
    print(exc.code)
    print(exc.read().decode())
except Exception as exc:
    print(type(exc).__name__, exc)