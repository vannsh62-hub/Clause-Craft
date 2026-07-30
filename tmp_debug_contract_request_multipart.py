import http.client
import mimetypes
import uuid

boundary = '----WebKitFormBoundary' + uuid.uuid4().hex
body = []

fields = {
    'request': 'Test contract creation via multipart'
}
for name, value in fields.items():
    body.append(f'--{boundary}')
    body.append(f'Content-Disposition: form-data; name="{name}"')
    body.append('')
    body.append(value)
body.append(f'--{boundary}--')
body.append('')
body_bytes = '\r\n'.join(body).encode('utf-8')

conn = http.client.HTTPConnection('127.0.0.1', 8000)
headers = {
    'Content-Type': f'multipart/form-data; boundary={boundary}',
    'Content-Length': str(len(body_bytes)),
}
conn.request('POST', '/api/v1/contracts', body_bytes, headers)
resp = conn.getresponse()
print(resp.status)
print(resp.read().decode('utf-8'))
conn.close()
