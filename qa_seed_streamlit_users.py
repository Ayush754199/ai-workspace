import os,json,hashlib
from pymongo import MongoClient
from argon2 import PasswordHasher
from datetime import datetime,timezone
from bson import ObjectId
c=MongoClient(os.environ['MONGODB_URL']); col=c[os.environ['MONGODB_DATABASE']]['users']; ph=PasswordHasher();out=[]
for name,password,admin in [('admin',os.environ['QA_ADMIN_PASSWORD'],True),('Aditya78',os.environ['QA_USER_PASSWORD'],False)]:
    x=col.find_one({'username':name})
    if x and not x.get('qa_local_only'):
        state='pre-existing non-QA username left untouched'
    else:
        fields={'username':name,'identifier':name,'full_name':'QA Admin' if admin else 'QA User','role':'admin' if admin else 'user','is_admin':admin,'is_active':True,'hashed_password':ph.hash(password),'qa_local_only':True,'updated_at':datetime.now(timezone.utc)}
        if x: col.update_one({'_id':x['_id']},{'$set':fields}); state='refreshed tagged local QA account'
        else: fields['_id']=ObjectId(hashlib.sha256(name.encode()).hexdigest()[:24]);fields['created_at']=datetime.now(timezone.utc);col.insert_one(fields);state='created local QA account'
    out.append({'username':name,'state':state})
print(json.dumps(out,indent=2));c.close()
