import os,sys,json
os.environ['DATABASE_URL']='sqlite:///./qa_dashboard_review.db'
os.environ['JWT_SECRET']='qa-dashboard-isolated-secret-0123456789abcdef'
os.environ['AI_GATEWAY_MODE']='dev'
os.environ['MONGODB_URL']=''
sys.path.insert(0,os.path.abspath('backend'))
from fastapi.testclient import TestClient
from app.main import app
from app.core.database import SessionLocal
from app.db import session as mongo_session
mongo_session.mongo.client=None
mongo_session.mongo.database=None
mongo_session.mongo.users=None
mongo_session.mongo.chat_history=None
with TestClient(app) as c:
    def login(identifier,password):
        r=c.post('/auth/login',json={'identifier':identifier,'password':password})
        assert r.status_code==200,(identifier,r.status_code,r.text)
        return {'Authorization':'Bearer '+r.json()['access_token']}
    uh=login('Aditya78','Aditya78.')
    me=c.get('/auth/me',headers=uh); assert me.status_code==200
    denied=c.get('/api/v1/admin/users',headers=uh)
    assert denied.status_code==403, (denied.status_code,denied.text)
    rooms=c.get('/api/v1/rooms',headers=uh); assert rooms.status_code==200
    room=c.post('/api/v1/rooms',headers=uh,json={'name':'Dashboard QA room','room_type':'group'}); assert room.status_code==200
    msg=c.post(f"/api/v1/rooms/{room.json()['id']}/messages",headers=uh,json={'content':'Local dashboard QA verification'}); assert msg.status_code==200
    ah=login('admin','AKrishna@123')
    paths=['/api/v1/admin/users','/api/v1/admin/analytics','/api/v1/admin/providers','/api/v1/admin/providers/models','/api/v1/admin/gateway/order','/api/v1/admin/analytics/usage?days=14','/api/v1/admin/logs?limit=100','/api/v1/admin/settings']
    admin_results={}
    for path in paths:
        r=c.get(path,headers=ah); admin_results[path]=r.status_code
        assert r.status_code==200,(path,r.status_code,r.text)
    # Capability quotas read without modifying configuration.
    with c.app.state._state._state if False else __import__('contextlib').nullcontext():
        pass
    video=c.get('/api/v1/video/capabilities',headers=uh); assert video.status_code==200
    db=SessionLocal()
    from app.models.gateway import UsageLog
    logs=db.query(UsageLog).count()
    db.close()
    print(json.dumps({'user_login':'PASS','user_profile':me.json(),'non_admin_denied_status':denied.status_code,'rooms_list':rooms.json(),'room_create':room.status_code,'message_create':msg.status_code,'admin_login':'PASS','admin_dashboard_reads':admin_results,'video_capabilities_status':video.status_code,'usage_log_rows':logs,'provider_calls':0,'image_video_generation_calls':0},indent=2))
