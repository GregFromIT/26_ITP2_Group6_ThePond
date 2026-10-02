"""Reduced Redduck workflow: no real hypervisor/Docker operations or readiness checks."""
import json
from pathlib import Path
from types import SimpleNamespace
from copy import deepcopy
import pytest
from sqlalchemy import select
from jsonschema import Draft202012Validator
from test_upload_routes import env,client,create,post
from test_submission_review import review
from db import db,Challenge,ChallengeFlag,ChallengeInstance,VMInstance,VMTemplate,ChallengeSubmission,UserSolve
from db.challenge_template_models import ChallengeTemplate
from challenge_ingestion.requirements import get_manifest_schema
from challenge_ingestion.runtime import process_once
from app import themes,container_lab
from app.scoring import hash_flag,challenge_progress,category_leaderboard


class Docker:
    def __init__(self):
        self.started=[];self.stopped=[];self.fail_start=False;self.fail_stop=False
    def start(self, **data):
        self.started.append(data)
        if self.fail_start:raise RuntimeError('test private error')
        return 'session-'+data['operation_key']
    def stop(self, **data):
        self.stopped.append(data)
        if self.fail_stop:raise RuntimeError('test private error')
        return True


@pytest.fixture
def lab(env,monkeypatch):
    app,_,root=env
    adapter=Docker();app.config['CONTAINER_LAB_ADAPTER']=adapter
    calls=[]
    def clone(vmid,node,label,**kwargs):
        calls.append(('clone',vmid,kwargs))
        instance=VMInstance(instance_id=kwargs['instance_id'],template_id=kwargs['template_id'],
            challenge_template_id=kwargs['challenge_template_id'],proxmox_vmid=9000+len(calls),
            proxmox_node=node,status='running')
        db.session.add(instance);db.session.commit()
        return SimpleNamespace(vmid=instance.proxmox_vmid,node=node)
    def stop(vmid,node,**kwargs):calls.append(('stop',vmid,kwargs))
    monkeypatch.setattr(themes,'clone_and_start',clone)
    monkeypatch.setattr(themes,'stop_and_destroy',stop)
    for name in ['create_session_vnet','get_console_ticket','enable_vm_firewall','apply_network_rule']:
        monkeypatch.setattr(themes,name,lambda *a,**k:pytest.fail('Unexpected operation'))
    with app.app_context():
        t=VMTemplate(template_name='redduck',proxmox_template_vmid=1200,proxmox_node='test')
        db.session.add(t);db.session.flush()
        ids=[]
        for name,key in [('First','bsides-first'),('Second','bsides-second')]:
            c=Challenge(title=name,description=name,instructions='Use the workstation.',category='labs',
                        execution_type='container_lab',docker_challenge_key=key,status='published')
            db.session.add(c);db.session.flush();ids.append(c.challenge_id)
            db.session.add(ChallengeTemplate(challenge_id=c.challenge_id,template_id=t.template_id,vm_role='workstation'))
            db.session.add(ChallengeFlag(challenge_id=c.challenge_id,flag_name='Answer',flag_hash=hash_flag(name),points=100))
        db.session.commit()
    return app,ids,adapter,calls


def launch(app,cid,user=4):
    return client(app,user).post(f'/themes/challenges/{cid}/launch',data={'_csrf':'test-csrf'})


def test_two_challenges_share_redduck_and_grade_separately(lab):
    app,ids,adapter,calls=lab
    assert launch(app,ids[0]).status_code==302
    assert launch(app,ids[1],user=2).status_code==302
    assert len(adapter.started)==2 and len({r['operation_key'] for r in adapter.started})==2
    assert [r['challenge_key'] for r in adapter.started]==['bsides-first','bsides-second']
    assert [c[1] for c in calls]==[1200,1200]
    with app.app_context():
        assert len(list(db.session.scalars(select(VMTemplate))))==1
        rows=list(db.session.scalars(select(ChallengeInstance).order_by(ChallengeInstance.instance_id)))
        assert all(r.status=='running' and r.docker_status=='active' for r in rows)
        instance_id=rows[0].instance_id
        # Title changes must not affect the stored lookup/session identity.
        db.session.get(Challenge,ids[0]).title='Renamed'
        db.session.commit()
    c=client(app,4)
    page=c.get('/themes/labs').data
    assert b'Open console' in page and b'Redduck workstation' in page
    assert b'bsides-first' not in page and b'session-pond-' not in page
    assert c.post(f'/themes/session/{instance_id}/flag',data={'_csrf':'test-csrf','flag':'Second'}).status_code==302
    with app.app_context():assert db.session.scalar(select(UserSolve)) is None
    assert c.post(f'/themes/session/{instance_id}/flag',data={'_csrf':'test-csrf','flag':'First'}).status_code==302
    with app.app_context():
        row=db.session.get(ChallengeInstance,instance_id)
        assert row.status=='complete' and row.docker_status=='closed'
        assert challenge_progress(4,ids[0])['points_earned']==100
        assert challenge_progress(4,ids[1])['points_earned']==0
        assert category_leaderboard('labs')[0]['score']==100
    assert adapter.stopped[0]['session_ref'].startswith('session-pond-')
    assert adapter.stopped[0]['operation_key']==adapter.started[0]['operation_key']


def test_missing_adapter_does_not_clone(lab):
    app,ids,adapter,calls=lab;app.config['CONTAINER_LAB_ADAPTER']=None
    assert launch(app,ids[0]).status_code==302
    assert calls==[]
    with app.app_context():assert db.session.scalar(select(ChallengeInstance)) is None


def test_lost_docker_start_response_is_cleaned_by_operation_key(lab):
    app,ids,adapter,calls=lab;adapter.fail_start=True
    assert launch(app,ids[0]).status_code==302
    assert adapter.stopped[0]['session_ref'] is None
    assert adapter.stopped[0]['operation_key']==adapter.started[0]['operation_key']
    assert any(c[0]=='stop' for c in calls)
    with app.app_context():
        row=db.session.scalar(select(ChallengeInstance))
        assert row.status=='abandoned' and row.docker_status=='closed'
        assert 'private' not in (row.error_message or '')


def test_cleanup_failure_retains_reference_and_can_retry(lab):
    app,ids,adapter,calls=lab
    launch(app,ids[0]);adapter.fail_stop=True
    with app.app_context():iid=db.session.scalar(select(ChallengeInstance.instance_id))
    c=client(app,4)
    c.post(f'/themes/session/{iid}/close',data={'_csrf':'test-csrf','outcome':'abandoned'})
    with app.app_context():
        row=db.session.get(ChallengeInstance,iid)
        assert row.docker_status=='cleanup_pending' and row.docker_session_ref
    adapter.fail_stop=False
    c.post(f'/themes/session/{iid}/close',data={'_csrf':'test-csrf'})
    with app.app_context():assert db.session.get(ChallengeInstance,iid).docker_status=='closed'


def test_other_user_cannot_close_or_submit_for_attempt(lab):
    app,ids,adapter,calls=lab;launch(app,ids[0])
    with app.app_context():iid=db.session.scalar(select(ChallengeInstance.instance_id))
    for action in ['close','flag']:
        assert client(app,2).post(f'/themes/session/{iid}/{action}',data={'_csrf':'test-csrf','flag':'First'}).status_code==404
    assert not adapter.stopped


def test_repeated_launch_does_not_create_another_environment(lab):
    app,ids,adapter,calls=lab
    launch(app,ids[0]);launch(app,ids[0])
    assert len(adapter.started)==1 and len(calls)==1


def test_standard_vm_launch_uses_assignments_without_docker(lab):
    app,ids,adapter,calls=lab
    with app.app_context():
        row=db.session.get(Challenge,ids[0]);row.execution_type='vm';row.docker_challenge_key=None;db.session.commit()
    launch(app,ids[0])
    assert len(calls)==1 and not adapter.started


def test_offline_uses_no_vm_or_docker_and_can_finish_unscored(lab):
    app,ids,adapter,calls=lab
    with app.app_context():
        c=Challenge(title='Physical',description='Physical',instructions='Use your cards.',category='labs',execution_type='offline',status='published')
        db.session.add(c);db.session.commit();cid=c.challenge_id
    launch(app,cid)
    with app.app_context():iid=db.session.scalar(select(ChallengeInstance.instance_id))
    c=client(app,4)
    page=c.get('/themes/labs').data
    assert b'Use your cards.' in page and b'Open console' not in page
    assert c.get(f'/themes/session/{iid}/console').status_code==404
    c.post(f'/themes/session/{iid}/close',data={'_csrf':'test-csrf','outcome':'complete'})
    assert not calls and not adapter.started
    with app.app_context():assert db.session.get(ChallengeInstance,iid).status=='complete'


@pytest.mark.parametrize('kind',['container_lab','offline'])
def test_submission_to_publication_uses_metadata_only(env,kind):
    app,_,root=env
    if kind=='container_lab':
        with app.app_context():
            db.session.add(VMTemplate(template_id=1,template_name='redduck',proxmox_template_vmid=1200,proxmox_node='test'))
            db.session.commit()
    manifest=json.loads((Path(__file__).parents[1]/f'examples/challenge_uploads/{kind}.json').read_text())
    cid=create(client(app),manifest);post(client(app),cid,'submit')
    process_once(app,worker_id='test')
    assert review(client(app,3),app,cid).status_code==303
    with app.app_context():digest=db.session.get(ChallengeSubmission,cid).content_digest
    assert client(app,3).post(f'/admin/challenge-submissions/{cid}/publish',data={'_csrf':'test-csrf','content_digest':digest}).status_code==303
    process_once(app,worker_id='test')
    with app.app_context():
        row=db.session.get(ChallengeSubmission,cid)
        assert row.status=='published'
        c=db.session.get(Challenge,row.published_challenge_id)
        assert c.execution_type==kind
        assert db.session.scalar(select(ChallengeFlag)).challenge_id==c.challenge_id
        mappings=list(db.session.scalars(select(ChallengeTemplate)))
        assert len(mappings)==(1 if kind=='container_lab' else 0)


@pytest.mark.parametrize('change',[{'workstation_template_id':0},{'docker_challenge_key':'../private'},
    {'docker_challenge_key':'a;run-command'},{'challenge_type':'non_vm'}])
def test_container_definition_rejects_malformed_identifiers(change):
    m=json.loads((Path(__file__).parents[1]/'examples/challenge_uploads/container_lab.json').read_text())
    m.update(change)
    assert list(Draft202012Validator(get_manifest_schema()).iter_errors(m))


def test_template_registration_is_repeatable_and_rejects_conflicts(lab, monkeypatch):
    from db.register_template import main
    app, ids, adapter, calls = lab
    monkeypatch.setattr('app.create_app', lambda: app)
    args = ['--name', 'redduck', '--vmid', '1200', '--node', 'test']
    main(args)
    main(args)
    with app.app_context():
        assert len(list(db.session.scalars(select(VMTemplate)))) == 1
    with pytest.raises(SystemExit) as error:
        main(['--name', 'different', '--vmid', '1200', '--node', 'test'])
    assert error.value.code == 2
    assert not calls and not adapter.started


def test_cleanup_cli_refuses_live_attempt_and_recovers_closed_attempt(lab, monkeypatch):
    from db.cleanup_docker_session import main
    app, ids, adapter, calls = lab
    monkeypatch.setattr('app.create_app', lambda: app)
    launch(app, ids[0])
    with app.app_context():
        iid = db.session.scalar(select(ChallengeInstance.instance_id))
    args = ['--instance-id', str(iid)]
    with pytest.raises(SystemExit) as error:
        main(args)
    assert error.value.code == 2
    assert not adapter.stopped
    adapter.fail_stop = True
    client(app, 4).post(f'/themes/session/{iid}/close', data={'_csrf': 'test-csrf'})
    with pytest.raises(SystemExit) as error:
        main(args)
    assert error.value.code == 1
    adapter.fail_stop = False
    main(args)
    with app.app_context():
        assert db.session.get(ChallengeInstance, iid).docker_status == 'closed'
