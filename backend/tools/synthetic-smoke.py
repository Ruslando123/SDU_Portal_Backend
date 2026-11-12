import http.cookiejar,json,os,time,urllib.request,urllib.error,urllib.parse,uuid
# Isolated local synthetic data only; credentials are supplied through the environment.
base='http://127.0.0.1:'+os.environ.get('SERVER_PORT','8080')
for attempt in range(50):
 try:
  with urllib.request.urlopen(base+'/actuator/health',timeout=2) as r:
   if r.status==200:break
 except OSError:time.sleep(.1)
else:raise RuntimeError('Backend did not become healthy')
class Browser:
 def __init__(self):self.opener=urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()));self.token=None
 def call(self,method,path,body=None,key=None,form=False):
  headers={}
  if self.token:headers['X-CSRF-TOKEN']=self.token
  if key:headers['Idempotency-Key']=key
  if body is not None:headers['Content-Type']='application/x-www-form-urlencoded' if form else 'application/json'
  req=urllib.request.Request(base+path,data=body,method=method,headers=headers)
  try:
   with self.opener.open(req,timeout=10) as r:return r.status,r.read().decode()
  except urllib.error.HTTPError as e:return e.code,e.read().decode()
 def csrf(self):
  status,raw=self.call('GET','/api/v1/auth/csrf');assert status==200;self.token=json.loads(raw)['token']
 def login(self,role):
  self.csrf();status,_=self.call('POST','/api/v1/auth/login',urllib.parse.urlencode({'username':os.environ[f'DEMO_{role}_LOGIN'],'password':os.environ[f'DEMO_{role}_PASSWORD']}).encode(),form=True);assert status==200,status;self.csrf()
student=Browser();student.login('STUDENT')
key=str(uuid.uuid4());section='10000000-0000-4000-8000-000000000008';term='10000000-0000-4000-8000-000000000002'
status,original=student.call('POST',f'/api/v1/sections/{section}/enrollments',key=key);assert status==201,(status,original)
id=json.loads(original)['enrollmentId']
status,schedule=student.call('GET',f'/api/v1/me/schedule?termId={term}');assert status==200 and json.loads(schedule)['credits']==3
status,withdrawn=student.call('DELETE',f'/api/v1/enrollments/{id}',key=str(uuid.uuid4()));assert status==200 and json.loads(withdrawn)['code']=='WITHDRAWN'
status,replay=student.call('POST',f'/api/v1/sections/{section}/enrollments',key=key);assert status==201 and original==replay
status,schedule=student.call('GET',f'/api/v1/me/schedule?termId={term}');assert status==200 and json.loads(schedule)['credits']==0
assert student.call('POST','/api/v1/auth/logout',b'')[0]==200
assert student.call('GET','/api/v1/me')[0]==401
admin=Browser();admin.login('ADMIN')
assert json.loads(admin.call('GET','/api/v1/me')[1])['role']=='ADMIN'
assert admin.call('POST',f'/api/v1/sections/{section}/enrollments',key=str(uuid.uuid4()))[0]==403
assert admin.call('POST','/api/v1/auth/logout',b'')[0]==200
print(json.dumps({'packaged_backend':'passed','student_login':200,'enrollment':201,'schedule':200,'withdrawal':200,'historical_replay':201,'logout':200,'old_session':401,'admin_login':200,'admin_student_command':403}))
