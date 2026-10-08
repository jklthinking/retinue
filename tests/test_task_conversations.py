"""Synthetic selected-range permissions, retention, withdrawal and legacy exits."""
import datetime as dt
import json
import sqlite3

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from server.app import create_app
from server.db import Actor, ApiToken, RuntimeSession, SessionCapture, Task, make_session_factory, migrate_database, LATEST_SCHEMA_VERSION
from server.engine import create_task
from server.security import hash_password, hash_token
from server.db import User

PASSWORD = 'synthetic-pass'


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.setenv('RETINUE_TASK_CONVERSATIONS','1')
    factory=make_session_factory(tmp_path/'test.db')
    with factory() as db:
        db.add_all([Actor(id='writer',kind='agent',display_name='Codex'),
                    Actor(id='reviewer',kind='agent',display_name='Claude',model='sample-model'),
                    Actor(id='human-owner',kind='human')])
        db.flush()
        for name in ['writer','reviewer']:
            db.add(ApiToken(actor_id=name,token_hash=hash_token('synthetic-'+name)))
        for name,role,actor in [('operator','admin',None),('member','member',None),('viewer','viewer',None),('owner','member','human-owner')]:
            db.add(User(username=name,password_hash=hash_password(PASSWORD),role=role,actor_id=actor))
        task=create_task(db,title='Synthetic review',created_by='human-owner',holder='writer')
        agent_task=create_task(db,title='Agent publication',created_by='writer',holder='writer')
        db.commit(); task_id=task.id; agent_task_id=agent_task.id
    client=TestClient(create_app(factory,data_dir=tmp_path))
    body={'runtime':'claude-code','external_id':'synthetic-dialogue','cursor':4,'privacy':'full',
          'title':'private needle title','summary':'private needle summary','message_count':4,'messages':[
              {'role':'user','text':'Please review the reading plan.','at':'2026-01-01T00:00:00Z'},
              {'role':'assistant','text':'First implement personal reading.','at':'2026-01-01T00:01:00Z'},
              {'role':'user','text':'How should progress work?','at':'2026-01-01T00:02:00Z'},
              {'role':'assistant','text':'Count estimated words. done','at':'2026-01-01T00:03:00Z'}]}
    result=client.post('/api/sessions/sync',json=body,headers=token('reviewer'))
    assert result.status_code==200
    session_id=result.json()['id']
    return client,factory,task_id,agent_task_id,session_id,body


def token(actor):return {'Authorization':'Bearer synthetic-'+actor}


def login(client,name):
    client.cookies.clear()
    assert client.post('/api/auth/login',json={'username':name,'password':PASSWORD}).status_code==200


def associate(client,task,session,**extra):
    return client.post(f'/api/tasks/{task}/conversations/links',json={'session_id':session,'sender_actor_id':'writer',**extra})


def read(client,task):return client.get(f'/api/tasks/{task}/conversations')


def prepare(data):
    client,_,task,_,session,_=data
    login(client,'operator'); response=associate(client,task,session)
    assert response.status_code==200
    return response.json()['id']


def test_identity_idempotency_selected_range_and_no_task_transition(setup):
    client,factory,task,_,session,_=setup
    prepare(setup)
    assert associate(client,task,session).json()['deduplicated']
    assert associate(client,task,session,capture_mode='live').status_code==409
    result=read(client,task)
    assert result.headers['cache-control']=='no-store'
    entry=result.json()['items'][0]
    assert entry['capture_mode']=='imported'
    assert [(m['sender']['name'],m['receiver']['name']) for m in entry['messages']]==[('Codex','Claude'),('Claude','Codex')]*2
    assert entry['receiver']['model_source']=='registry'
    assert 'hash' not in result.text
    with factory() as db:assert db.get(Task,task).status=='queued'
    login(client,'owner')
    assert len(read(client,task).json()['items'][0]['messages'])==4
    assert read(client,task).json()['items'][0]['summary'] is None
    assert client.get(f'/api/sessions/{session}').status_code==404  # Range ownership is not whole-source access.


@pytest.mark.parametrize('reader',['member','viewer','writer'])
def test_foreign_readers_legacy_search_and_flag_cannot_bypass(setup,monkeypatch,reader):
    client,_,task,_,session,body=setup
    prepare(setup)
    if reader=='writer':client.cookies.clear();client.headers.update(token(reader))
    else:login(client,reader)
    assert read(client,task).json()['items']==[]
    assert client.get(f'/api/sessions/{session}').status_code==404
    assert client.get('/api/sessions?q=private%20needle').json()==[]
    assert client.get(f'/api/sessions/{session}/captures').status_code==404
    assert client.post(f'/api/sessions/{session}/capture-obsidian',json={}).status_code==(403 if reader=='viewer' else 404)
    assert client.get('/api/session-captures/pending').json()==[]
    assert client.post(f'/api/sessions/{session}/create-task',json={'title':'copy','holder':'writer','dept':'test','acceptance':[]}).status_code==(403 if reader=='viewer' else 404)
    monkeypatch.setenv('RETINUE_TASK_CONVERSATIONS','0')
    assert read(client,task).status_code==404
    assert client.get(f'/api/sessions/{session}').status_code==404
    # A never-linked old source preserves legacy human reads.
    if reader!='writer':
        clone=dict(body,external_id='unprotected-sample')
        result=client.post('/api/sessions/sync',json=clone,headers=token('reviewer'))
        assert result.status_code==200
        assert client.get('/api/sessions/'+str(result.json()['id'])).status_code==200


def test_capture_cache_downgrade_with_stale_cursor_and_sync_scope(setup):
    client,factory,task,_,session,body=setup
    prepare(setup)
    capture=client.post(f'/api/sessions/{session}/capture-obsidian',json={}).json()
    assert 'private needle summary' in capture['markdown']
    login(client,'member')
    assert client.post('/api/sessions/sync',json={**body,'actor_id':'reviewer','cursor':5}).status_code==403
    assert client.post(f"/api/session-captures/{capture['id']}/exported",json={'target_path':'sample.md'}).status_code==404
    login(client,'operator')
    lower={**body,'privacy':'metadata','messages':[],'summary':'','cursor':1}
    assert client.post('/api/sessions/sync',json=lower,headers=token('reviewer')).status_code==200
    assert read(client,task).json()['items'][0]['state']=='metadata_only'
    assert read(client,task).json()['items'][0]['messages']==[]
    with factory() as db:
        assert db.get(SessionCapture,capture['id']).markdown==''
        assert db.get(SessionCapture,capture['id']).status=='withheld'
        assert db.get(RuntimeSession,session).cursor==4
    assert client.get('/api/session-captures/pending').json()==[]
    assert client.get(f'/api/sessions/{session}/captures').json()[0]['markdown']==''
    assert client.post(f'/api/sessions/{session}/capture-obsidian',json={}).status_code==410


def test_window_moves_changes_and_source_epoch_fail_closed(setup):
    client,factory,task,_,session,body=setup
    prepare(setup)
    # Appending/repositioning retains the selected subsequence, independent of indexes.
    shifted=[{'role':'user','text':'Earlier unrelated message','at':None}]+body['messages']
    updated={**body,'cursor':5,'message_count':5,'messages':shifted}
    assert client.post('/api/sessions/sync',json=updated,headers=token('reviewer')).status_code==200
    assert len(read(client,task).json()['items'][0]['messages'])==4
    updated['cursor']=6;updated['messages']=shifted[2:];updated['message_count']=6
    assert client.post('/api/sessions/sync',json=updated,headers=token('reviewer')).status_code==200
    entry=read(client,task).json()['items'][0]
    assert entry['state']=='outside_window' and entry['messages']==[] and entry['summary'] is None
    with factory() as db:
        db.get(RuntimeSession,session).external_id='different-source'
        db.commit()
    assert read(client,task).json()['items'][0]['state']=='source_changed'


def test_withdrawal_does_not_restore_legacy_access_and_new_link_gets_new_id(setup):
    client,_,task,_,session,_=setup
    original=prepare(setup)
    client.cookies.clear()
    assert client.post(f'/api/tasks/{task}/conversations/links/{original}/revoke',headers=token('reviewer')).status_code==200
    login(client,'operator')
    assert read(client,task).json()['items'][0]['state']=='revoked'
    new=associate(client,task,session).json()['id'];assert new!=original
    assert read(client,task).json()['items'][0]['messages']==[]
    login(client,'member')
    assert client.get(f'/api/sessions/{session}').status_code==404
    assert read(client,task).json()['items']==[]


@pytest.mark.parametrize('role',['member','viewer','reviewer'])
def test_association_roles_and_strict_body(setup,role):
    client,_,task,_,session,_=setup
    if role=='reviewer':client.headers.update(token(role))
    else:login(client,role)
    assert associate(client,task,session).status_code==403
    client.headers.clear();login(client,'operator')
    assert associate(client,task,session,msg_from=4).status_code==422
    assert associate(client,task,session,command='whoami').status_code==422
    assert client.post(f'/api/tasks/{task}/conversations/links',content=b' '*16385).status_code==413


def test_agent_publication_has_no_human_owner_and_role_revocation(setup):
    client,factory,_,task,session,_=setup
    login(client,'operator');assert associate(client,task,session).status_code==200
    login(client,'owner');assert read(client,task).json()['items']==[]
    login(client,'operator')
    with factory() as db:
        db.scalar(select(User).where(User.username=='operator')).role='member';db.commit()
    assert read(client,task).json()['items']==[]
    assert client.get(f'/api/sessions/{session}').status_code==404


def test_migration_27_adds_only_conversation_tables(tmp_path):
    path=tmp_path/'migration.db';make_session_factory(path)
    with sqlite3.connect(path) as db:
        db.execute('DROP TABLE task_conversation_links');db.execute('DROP TABLE conversation_protected_sources')
        db.execute('UPDATE schema_version SET version=27')
        before=dict(db.execute("SELECT name,sql FROM sqlite_master WHERE type='table'"))
    migrate_database(path)
    with sqlite3.connect(path) as db:
        after=dict(db.execute("SELECT name,sql FROM sqlite_master WHERE type='table'"))
        assert all(after[name]==ddl for name,ddl in before.items())
        assert db.execute('SELECT version FROM schema_version').fetchone()[0]==28==LATEST_SCHEMA_VERSION


def test_owner_receives_only_selected_range_and_all_withdrawn_stays_protected(setup, monkeypatch):
    client,_,task,_,session,_=setup
    login(client,'operator')
    link=associate(client,task,session,msg_from=1,msg_to=3).json()['id']
    login(client,'owner')
    messages=read(client,task).json()['items'][0]['messages']
    assert [m['text'] for m in messages]==['First implement personal reading.','How should progress work?']
    assert client.get(f'/api/sessions/{session}').status_code==404
    login(client,'operator')
    assert client.post(f'/api/tasks/{task}/conversations/links/{link}/revoke').status_code==200
    login(client,'member')
    monkeypatch.setenv('RETINUE_TASK_CONVERSATIONS','0')
    assert client.get(f'/api/sessions/{session}').status_code==404


def test_preexisting_capture_cannot_leak_to_foreign_readers_after_association(setup):
    client,_,task,_,session,_=setup
    login(client,'operator')
    capture=client.post(f'/api/sessions/{session}/capture-obsidian',json={}).json()
    associate(client,task,session)
    login(client,'member')
    assert client.get('/api/session-captures/pending').json()==[]
    assert client.get(f'/api/sessions/{session}/captures').status_code==404
    assert client.post(f"/api/session-captures/{capture['id']}/exported",json={}).status_code==404
    login(client,'operator')
    assert client.get(f'/api/sessions/{session}/captures').json()[0]['status']=='queued'


def test_content_change_and_unknown_identity_are_not_invented(setup):
    client,_,task,_,session,body=setup
    login(client,'operator')
    assert associate(client,task,session,sender_actor_id=None).status_code==200
    entry=read(client,task).json()['items'][0]
    assert entry['messages'][0]['sender']['name'] is None
    modified=json.loads(json.dumps(body));modified['cursor']=5;modified['messages'][0]['text']='Changed content'
    client.post('/api/sessions/sync',json=modified,headers=token('reviewer'))
    assert read(client,task).json()['items'][0]['state']=='outside_window'
    assert associate(client,task,session,session_id=9223372036854775808).status_code==422


def test_atomic_source_protection_and_false_never_unprotects(setup, monkeypatch):
    client,_,_,_,_,body=setup
    result=client.post('/api/sessions/sync',json={**body,'external_id':'private-on-first-sync','protect_conversation':True},headers=token('reviewer'))
    assert result.status_code==200
    source_id=result.json()['id']
    login(client,'member')
    assert client.get(f'/api/sessions/{source_id}').status_code==404
    assert client.post('/api/sessions/sync',json={**body,'actor_id':'reviewer','external_id':'other','protect_conversation':True}).status_code==403
    assert client.post('/api/sessions/sync',json={**body,'external_id':'private-on-first-sync','protect_conversation':False,'cursor':4},headers=token('reviewer')).status_code==200
    assert client.get(f'/api/sessions/{source_id}').status_code==404
    monkeypatch.setenv('RETINUE_TASK_CONVERSATIONS','0')
    assert client.post('/api/sessions/sync',json={**body,'external_id':'disabled-opt-in','protect_conversation':True},headers=token('reviewer')).status_code==403


def test_viewer_source_mapping_and_unbound_owner_name_do_not_grant_access(setup):
    client,factory,task,_,session,_=setup
    prepare(setup)
    with factory() as db:
        db.scalar(select(User).where(User.username=='viewer')).actor_id='reviewer'
        user=db.scalar(select(User).where(User.username=='owner'));user.actor_id=None;user.username='human-owner';db.commit()
    login(client,'viewer')
    assert read(client,task).json()['items']==[]
    assert client.get(f'/api/sessions/{session}').status_code==404
    assert client.get('/api/sessions?q=private%20needle').json()==[]
    login(client,'human-owner')
    assert read(client,task).json()['items']==[]


def test_revoke_when_disabled_briefing_title_and_postgres_partial_index(setup, monkeypatch):
    client,factory,task,_,session,_=setup
    link=prepare(setup)
    from server.engine import build_start_briefing
    from server.db import TaskConversationLink
    from sqlalchemy.schema import CreateIndex
    from sqlalchemy.dialects import postgresql
    with factory() as db:
        db.get(RuntimeSession,session).task_id=task;db.commit()
        assert build_start_briefing(db,db.get(Task,task),'writer')['related_sessions'][0]['title']=='受保护会话'
        assert build_start_briefing(db,db.get(Task,task),'reviewer')['related_sessions'][0]['title']=='private needle title'
    index=next(index for index in TaskConversationLink.__table__.indexes if index.name=='ux_task_conversation_active_selection')
    assert 'WHERE revoked_at IS NULL' in str(CreateIndex(index).compile(dialect=postgresql.dialect()))
    monkeypatch.setenv('RETINUE_TASK_CONVERSATIONS','0')
    assert client.post(f'/api/tasks/{task}/conversations/links/{link}/revoke').status_code==200
    login(client,'member')
    assert client.get(f'/api/sessions/{session}').status_code==404


def test_repeated_identical_sequences_fail_closed():
    from server.conversations import locate,message_hash
    message={'role':'user','at':None,'text':'identical'}
    assert locate([message,message],[message_hash(message)]) is None
