"""Publication orchestration tests. No real hypervisor operations."""
from datetime import timedelta
import pytest
from sqlalchemy import select, event, func
from test_upload_routes import env, client
from test_submission_review import ready, review
from db import db, ChallengeSubmission, SubmissionJob, SubmissionFile, Challenge, VMTemplate, ChallengeFlag, NotificationOutbox
from challenge_ingestion.publication import PublicationWorker, PublicationError
from challenge_ingestion.storage import QuarantineStorage
from challenge_ingestion.worker import LeaseLost


class Adapter:
    def __init__(self):
        self.resources = set()
        self.calls = 0
        self.healthy = True
    def plan(self, manifest):
        return [dict(role=v['role'], node='test-node', vmid=1200+i,
                     template_name='test-template', owned=v['source']['kind']=='image')
                for i,v in enumerate(manifest['vms'])]
    def prepare(self, manifest, journal, files, storage, checkpoint, guard):
        self.calls += 1
        assert files
        for p in journal:
            guard()
            self.resources.add(p['operation_key'])
            checkpoint(p['role'], state='ready', task_id='test-task')
        return True
    def ready(self, manifest, journal, guard):
        guard()
        return self.healthy


@pytest.fixture
def approved(ready):
    app, identifier, root = ready
    assert review(client(app,3),app,identifier).status_code == 303
    adapter = Adapter()
    app.config['INGESTION_PUBLICATION_ADAPTER'] = adapter
    with app.app_context(), QuarantineStorage(root/'quarantine') as storage:
        worker = PublicationWorker(db.engines['pond'], storage, worker_id='test-publish',
                                   adapter=adapter, inspect_image=lambda *_: True)
        digest = db.session.get(ChallengeSubmission,identifier).content_digest
        db.session.remove()
        yield app, identifier, root, worker, digest, adapter


def enqueue(case, **kwargs):
    app, identifier, root, worker, digest, adapter = case
    return worker.enqueue(identifier, actor_user_id=3, expected_digest=digest, **kwargs)


def test_publication_atomic_and_idempotent(approved):
    app, identifier, root, worker, digest, adapter = approved
    job = enqueue(approved)
    assert enqueue(approved) == job
    assert worker.run_once() == job
    assert worker.run_once() is None
    assert enqueue(approved) == job
    db.session.remove()
    row = db.session.get(ChallengeSubmission, identifier)
    assert row.status == 'published' and row.published_at
    challenge = db.session.get(Challenge,row.published_challenge_id)
    assert challenge.title == row.title and challenge.status == 'published'
    template = db.session.scalar(select(VMTemplate))
    assert template.challenge_id == challenge.challenge_id and template.proxmox_template_vmid == 1200
    flag = db.session.scalar(select(ChallengeFlag))
    assert flag.template_id == template.template_id and flag.points == 100
    assert db.session.scalar(select(func.count()).select_from(Challenge)) == 1
    assert db.session.get(SubmissionJob,job).status == 'succeeded'
    assert db.session.scalar(select(NotificationOutbox).where(NotificationOutbox.event_type=='published'))
    assert len(adapter.resources) == 1


def test_readiness_failure_retains_journal_and_retry_reuses_it(approved):
    *_, worker, digest, adapter = approved
    adapter.healthy = False
    job = enqueue(approved)
    worker.run_once()
    db.session.remove()
    assert db.session.get(SubmissionJob,job).status == 'failed'
    assert not db.session.scalar(select(Challenge))
    assert db.session.get(SubmissionJob,job).resource_inventory_json[0]['task_id'] == 'test-task'
    with pytest.raises(PublicationError): enqueue(approved)
    adapter.healthy = True
    assert enqueue(approved,retry=True) == job
    worker.run_once()
    db.session.remove()
    assert db.session.get(SubmissionJob,job).status == 'succeeded'
    assert len(adapter.resources)==1 and adapter.calls==2


def test_changed_bytes_fail_before_external_work(approved):
    app, identifier, root, worker, digest, adapter = approved
    job = enqueue(approved)
    file = db.session.scalar(select(SubmissionFile))
    (root/'quarantine'/file.storage_key).write_bytes(b'corrupted')
    worker.run_once()
    db.session.remove()
    assert db.session.get(SubmissionJob,job).status == 'failed'
    assert not adapter.resources and not db.session.scalar(select(Challenge))


def test_changed_manifest_blocks_enqueue(approved):
    app, identifier, root, worker, digest, adapter = approved
    row=db.session.get(ChallengeSubmission,identifier)
    row.manifest_json={**row.manifest_json,'title':'changed'}
    db.session.commit()
    with pytest.raises(PublicationError): enqueue(approved)


def test_expired_worker_requires_reconciliation_and_cannot_checkpoint(approved):
    app, identifier, root, worker, digest, adapter = approved
    job=enqueue(approved)
    claim=worker.claim()
    row=db.session.get(SubmissionJob,job)
    row.lease_expires_at=worker._now()-timedelta(seconds=1)
    db.session.commit()
    assert worker.claim() is None
    with pytest.raises(LeaseLost): worker.checkpoint(claim,'target',state='ready')
    db.session.remove()
    assert db.session.get(SubmissionJob,job).error_code=='LEASE_EXPIRED_RECONCILE'
    assert enqueue(approved,retry=True)==job


def test_web_permissions_csrf_configuration_and_stale_digest(approved):
    app, identifier, root, worker, digest, adapter = approved
    url=f'/admin/challenge-submissions/{identifier}/publish'
    data={'_csrf':'test-csrf','content_digest':digest}
    assert client(app,1).post(url,data=data).status_code==403
    assert client(app,3).post(url,data={**data,'_csrf':'bad'}).status_code==400
    assert client(app,3).post(url,data={**data,'content_digest':'bad'}).status_code==400
    app.config['INGESTION_PUBLICATION_ADAPTER']=None
    assert client(app,3).post(url,data=data).status_code==400
    app.config['INGESTION_PUBLICATION_ADAPTER']=adapter
    assert client(app,3).post(url,data=data).status_code==303


def test_retry_requires_operator_acknowledgement(approved):
    app, identifier, root, worker, digest, adapter = approved
    adapter.healthy=False
    enqueue(approved); worker.run_once()
    url=f'/admin/challenge-submissions/{identifier}/publish'
    data={'_csrf':'test-csrf','content_digest':digest,'retry':'yes'}
    assert client(app,3).post(url,data=data).status_code==400
    assert client(app,3).post(url,data={**data,'reconciled':'yes'}).status_code==303


def test_database_failure_rolls_back_all_live_records(approved):
    app, identifier, root, worker, digest, adapter = approved
    job=enqueue(approved)
    def fail(conn,cursor,statement,parameters,context,many):
        if statement.startswith('INSERT INTO challenge_flags'):
            raise RuntimeError('test database failure')
    event.listen(worker.engine,'before_cursor_execute',fail)
    try: worker.run_once()
    finally: event.remove(worker.engine,'before_cursor_execute',fail)
    db.session.remove()
    assert not db.session.scalar(select(Challenge))
    assert not db.session.scalar(select(VMTemplate))
    assert db.session.get(SubmissionJob,job).status=='failed'
    assert db.session.get(SubmissionJob,job).resource_inventory_json


@pytest.mark.parametrize('field,value',[('vmid',True),('vmid',99),('owned',False),('role','unknown'),('node','')])
def test_unsafe_plan_never_prepares_resources(approved,field,value):
    app, identifier, root, worker, digest, adapter = approved
    original=adapter.plan
    def invalid(manifest):
        plans=original(manifest); plans[0][field]=value; return plans
    adapter.plan=invalid
    job=enqueue(approved);worker.run_once()
    db.session.remove()
    assert db.session.get(SubmissionJob,job).status=='failed'
    assert not adapter.resources


def test_concurrent_enqueue_has_single_job(approved):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    barrier=Barrier(2)
    def queue(_):
        barrier.wait()
        return enqueue(approved)
    with ThreadPoolExecutor(max_workers=2) as pool:
        ids=list(pool.map(queue,[1,2]))
    assert ids[0]==ids[1]


def test_existing_live_vmid_blocks_preparation(approved):
    app, identifier, root, worker, digest, adapter=approved
    other=Challenge(title='Existing',description='existing',instructions='existing',status='published')
    db.session.add(other);db.session.flush()
    db.session.add(VMTemplate(challenge_id=other.challenge_id,template_name='Existing',
        proxmox_template_vmid=1200,proxmox_node='test-node',vm_role='existing'))
    db.session.commit()
    job=enqueue(approved);worker.run_once()
    db.session.remove()
    assert db.session.get(SubmissionJob,job).status=='failed'
    assert adapter.calls==0
    assert db.session.scalar(select(func.count()).select_from(Challenge))==1


def test_missing_approval_evidence_blocks_enqueue(approved):
    from db import AuditLog
    from sqlalchemy import delete
    db.session.execute(delete(AuditLog).where(AuditLog.action=='submission_review'))
    db.session.commit()
    with pytest.raises(PublicationError): enqueue(approved)


def test_failed_upload_correction_through_publication(env):
    from test_upload_routes import create, post, upload
    from challenge_ingestion.runtime import process_once
    from db.challenge_models import NetworkRule
    app, manifest, root=env
    manifest['network_rules']=[dict(from_role='target',to_role='target',protocol='tcp',port=80)]
    app.config.update(INGESTION_INSPECT_IMAGE=lambda *_: True,INGESTION_PUBLICATION_ADAPTER=Adapter())
    c=client(app)
    first=create(c,manifest)
    post(c,first,'submit')
    process_once(app,worker_id='e2e')
    response=post(c,first,'revise')
    assert response.status_code==303
    revision=int(response.location.rsplit('/',1)[-1])
    assert upload(c,revision).status_code==201
    post(c,revision,'submit')
    process_once(app,worker_id='e2e')
    assert review(client(app,3),app,revision).status_code==303
    with app.app_context():
        digest=db.session.get(ChallengeSubmission,revision).content_digest
    assert client(app,3).post(f'/admin/challenge-submissions/{revision}/publish',data={
        '_csrf':'test-csrf','content_digest':digest}).status_code==303
    process_once(app,worker_id='e2e')
    with app.app_context():
        assert db.session.get(ChallengeSubmission,first).status=='needs_changes'
        row=db.session.get(ChallengeSubmission,revision)
        assert row.status=='published' and row.supersedes_submission_id==first
        rule=db.session.scalar(select(NetworkRule))
        assert rule.challenge_id==row.published_challenge_id and rule.port==80


def test_failed_publication_can_return_for_correction_without_losing_journal(approved):
    from test_upload_routes import post
    app, identifier, root, worker, digest, adapter=approved
    adapter.healthy=False
    job=enqueue(approved);worker.run_once()
    url=f'/admin/challenge-submissions/{identifier}/return-for-correction'
    data={'_csrf':'test-csrf','content_digest':digest,'note':'Fix the image boot configuration.'}
    assert client(app,3).post(url,data=data).status_code==400
    data['reconciled']='yes'
    assert client(app,1).post(url,data=data).status_code==403
    assert client(app,3).post(url,data={**data,'note':''}).status_code==400
    assert client(app,3).post(url,data=data).status_code==303
    db.session.remove()
    row=db.session.get(ChallengeSubmission,identifier)
    assert row.status=='rejected' and row.approved_at is None
    assert db.session.get(SubmissionJob,job).resource_inventory_json
    assert post(client(app,1),identifier,'revise').status_code==303


def test_running_publication_cannot_be_returned(approved):
    app, identifier, root, worker, digest, adapter=approved
    enqueue(approved)
    with pytest.raises(PublicationError):
        worker.return_for_correction(identifier,actor_user_id=3,expected_digest=digest,note='Fix')
