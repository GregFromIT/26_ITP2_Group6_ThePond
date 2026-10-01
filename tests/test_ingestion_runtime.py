from test_upload_routes import env, client, create, post, upload
from challenge_ingestion.runtime import check_configuration, process_once
from db import db, ChallengeSubmission


def test_configuration_has_no_success_defaults():
    assert not any(check_configuration({}).values())


def test_worker_delivers_missing_file_findings_without_publication(env):
    app, manifest, root = env
    c=client(app)
    identifier=create(c,manifest)
    assert post(c,identifier,'submit').status_code==303
    result=process_once(app,worker_id='integration')
    assert result['validation_job'] and result['publication_disabled']
    assert result['notifications_sent']==1
    with app.app_context():
        row=db.session.get(ChallengeSubmission,identifier)
        assert row.status=='needs_changes'
    page=c.get('/notifications')
    assert page.status_code==200


def test_trusted_settings_loaded_before_test_overrides(env, tmp_path, monkeypatch):
    from app import create_app
    app, _, _=env
    settings=tmp_path/'settings.py'
    settings.write_text('INGESTION_VERIFY_TEMPLATE = lambda vm: False\nUPLOAD_MAX_IMAGE_BYTES = 123\n')
    monkeypatch.setenv('POND_SETTINGS',str(settings))
    configured=create_app(dict(app.config,UPLOAD_MAX_IMAGE_BYTES=456))
    # Explicit config overrides win, including the app's None verifier.
    assert configured.config['UPLOAD_MAX_IMAGE_BYTES']==456
    assert configured.config['INGESTION_VERIFY_TEMPLATE'] is None
    overrides=dict(app.config)
    del overrides['INGESTION_VERIFY_TEMPLATE']
    configured=create_app(overrides)
    assert configured.config['INGESTION_VERIFY_TEMPLATE']({}) is False
