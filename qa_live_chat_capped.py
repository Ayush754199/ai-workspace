import os,sys,json,time
os.environ['DATABASE_URL']='sqlite:///./qa_live_chat_capped.db'
os.environ['JWT_SECRET']='qa-live-review-only-secret-0123456789abcdef'
os.environ['AI_GATEWAY_MODE']='live'
os.environ['MONGODB_URL']=''
os.environ['AI_DAILY_QUOTA']='2'
os.environ['AI_PER_CHAT_QUOTA']='2'
os.environ['AI_IMAGE_DAILY_QUOTA']='2'
os.environ['AI_VIDEO_DAILY_QUOTA']='2'
sys.path.insert(0,os.path.abspath('backend'))
from fastapi.testclient import TestClient
from app.main import app
from app.core.config import settings
from app.core.database import Base,engine,SessionLocal
from app.core.security import hash_password
from app.db import session as mongo_session
from app.models.user import User
from app.models.gateway import UserQuota,UsageLog
from app.services.ai_gateway.gateway import AIGateway
mongo_session.mongo.client=None
mongo_session.mongo.database=None
mongo_session.mongo.users=None
mongo_session.mongo.chat_history=None
settings.AI_GATEWAY_MODE='live'
settings.AI_REQUEST_TIMEOUT_SECONDS=30
Base.metadata.create_all(bind=engine)
db=SessionLocal()
user=db.query(User).filter_by(identifier='Aditya78').first()
if user is None:
    user=User(identifier='Aditya78',email='live-check-user@local.test',full_name='Isolated Live Check User',hashed_password=hash_password('Aditya78.'),role='user',is_active=True,is_admin=False)
    db.add(user);db.commit();db.refresh(user)
user_id=user.id
quota=db.query(UserQuota).filter_by(user_id=user_id).first()
if quota is None:
    quota=UserQuota(user_id=user_id,daily_limit=2,daily_used=0,reserved=0,per_chat_limit=2)
    db.add(quota);db.commit()
else:
    quota.daily_limit=2;quota.daily_used=0;quota.reserved=0;quota.per_chat_limit=2;db.commit()
route_info={'configured_openrouter_credentials':bool(__import__('backend.key_rotator',fromlist=['get_provider_credentials']).get_provider_credentials('OpenRouter')),'eligible_routes':[]}
try:
    gateway=AIGateway(); routes=gateway.eligible_routes('chat',db=db); route_info['eligible_routes']=[{'provider':x.provider,'model':x.model} for x in routes]
finally: db.close()
with TestClient(app) as c:
    login=c.post('/auth/login',json={'identifier':'Aditya78','password':'Aditya78.'})
    result={'live_provider_attempted':False,'chat_status':None,'provider':None,'model':None,'quota_daily_limit':2,'quota_consumed':0,'max_local_quota_fraction':0.5,'provider_metadata':route_info}
    if login.status_code!=200:
        result['blocked']='Local test-account login failed; no live provider request made.'
    elif not route_info['eligible_routes']:
        result['blocked']='No verified eligible live free chat route; no provider request made.'
    else:
        h={'Authorization':'Bearer '+login.json()['access_token']}
        conv=c.post('/api/v1/chat/v2/conversations',headers=h,json={'title':'One-call live chat QA'})
        if conv.status_code!=200:
            result['blocked']='Could not create isolated local test conversation; no provider request made.'
        else:
            result['live_provider_attempted']=True
            start=time.perf_counter()
            try:
                response=c.post(f"/api/v1/chat/v2/conversations/{conv.json()['id']}/messages",headers=h,json={'content':'Reply with only: live check complete.'})
                result['chat_status']=response.status_code
                if response.status_code==200:
                    result['provider']=response.json().get('provider');result['model']=response.json().get('model')
                else:
                    # Do not print upstream body/details; retain only HTTP status.
                    result['provider_error']='Provider request did not succeed; response details redacted.'
            except Exception:
                result['provider_error']='Provider call raised an exception; details redacted.'
            result['elapsed_seconds']=round(time.perf_counter()-start,2)
    db=SessionLocal()
    q=db.query(UserQuota).filter_by(user_id=user_id).first()
    result['quota_consumed']=q.daily_used if q else 0
    result['quota_reserved']=q.reserved if q else 0
    result['usage_log_count']=db.query(UsageLog).filter_by(user_id=user_id).count()
    db.close()
    result['provider_key_values_printed']=False
    result['paid_provider_enabled']=bool(settings.PAID_PROVIDERS_ENABLED)
    print(json.dumps(result,indent=2))
