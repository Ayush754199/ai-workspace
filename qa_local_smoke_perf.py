import os, sys, time, statistics, json
os.environ['DATABASE_URL'] = 'sqlite:///./qa_local_runtime.db'
os.environ['JWT_SECRET'] = 'qa-runtime-only-secret-0123456789abcdef'
os.environ['AI_GATEWAY_MODE'] = 'dev'
sys.path.insert(0, os.path.abspath('backend'))
from fastapi.testclient import TestClient
from app.main import app
from app.core.database import SessionLocal
from app.models.user import User
from app.core import security
with TestClient(app) as c:
    lat=[]
    for i in range(60):
        t=time.perf_counter(); r=c.get('/api/v1/health'); lat.append((time.perf_counter()-t)*1000)
        assert r.status_code == 200
    def pct(p): return round(sorted(lat)[min(len(lat)-1, int((len(lat)-1)*p))],2)
    health={'iterations':len(lat),'status':'all 200','avg_ms':round(statistics.mean(lat),2),'p50_ms':pct(.5),'p95_ms':pct(.95),'max_ms':round(max(lat),2)}
    email='manualqa_user@local.test'
    db=SessionLocal()
    try:
        user=db.query(User).filter(User.email==email).first()
        if user: db.delete(user); db.commit()
        user=User(identifier=email,email=email,hashed_password=security.hash_password('LocalQA!Pass123'),full_name='Local QA User',role='user',is_active=True,is_admin=False)
        db.add(user); db.commit(); db.refresh(user); uid=user.id
        admin=User(identifier='manualqa_admin@local.test',email='manualqa_admin@local.test',hashed_password=security.hash_password('LocalQA!Admin123'),full_name='Local QA Admin',role='admin',is_active=True,is_admin=True)
        existing=db.query(User).filter(User.email==admin.email).first()
        if existing: db.delete(existing); db.commit()
        db.add(admin); db.commit(); db.refresh(admin); aid=admin.id
    finally: db.close()
    u=c.post('/auth/login',json={'identifier':email,'password':'LocalQA!Pass123'})
    bad=c.post('/auth/login',json={'identifier':email,'password':'invalid-password'})
    if u.status_code==200:
        token=u.json()['access_token']; hdr={'Authorization':'Bearer '+token}
        me=c.get('/auth/me',headers=hdr)
        denied=c.get('/api/v1/admin/users',headers=hdr)
        rooms=c.get('/api/v1/rooms',headers=hdr)
        room=c.post('/api/v1/rooms',headers=hdr,json={'name':'QA Local Room','room_type':'group'})
        msgs=None
        if room.status_code==200:
            rid=room.json()['id']; msgs=c.post(f'/api/v1/rooms/{rid}/messages',headers=hdr,json={'content':'safe local manual QA message'})
        ah=c.post('/auth/login',json={'identifier':'manualqa_admin@local.test','password':'LocalQA!Admin123'})
        admin_status=None
        if ah.status_code==200:
            at=ah.json()['access_token']; admin_status=c.get('/api/v1/admin/users',headers={'Authorization':'Bearer '+at})
    else:
        me=denied=rooms=room=msgs=ah=admin_status=None
    out={'health_performance':health,'manual_api_smoke':{'user_login':u.status_code,'invalid_login':bad.status_code,'profile':me.status_code if me else None,'admin_denied_for_user':denied.status_code if denied else None,'rooms_list':rooms.status_code if rooms else None,'room_create':room.status_code if room else None,'room_message':msgs.status_code if msgs else None,'admin_login':ah.status_code if ah else None,'admin_users_list':admin_status.status_code if admin_status else None},'quota_or_paid_ai_calls':0}
    print(json.dumps(out,indent=2))
