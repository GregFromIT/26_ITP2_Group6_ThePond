from pathlib import Path
import sqlite3
import pytest
from db.migrate_container_labs import migrate, MigrationError


@pytest.fixture
def old(tmp_path):
    path = tmp_path/'old.db'
    with sqlite3.connect(path) as conn:
        conn.executescript((Path(__file__).parent/'fixtures/pond_before_container_labs.sql').read_text())
    return path


def test_migration_preserves_accounts_scores_flags_and_runtime_links(old,tmp_path):
    before = old.read_bytes()
    assert migrate(old)['state']=='old'
    assert old.read_bytes()==before
    backup=tmp_path/'backup.db'
    result=migrate(old,apply=True,backup=backup,confirm_existing_vm=True)
    assert result['changed'] and result['assignments_created']==1
    with sqlite3.connect(old) as c:
        assert c.execute('select username from users').fetchone()[0]=='migration-student'
        assert c.execute('select password_hash from user_credentials').fetchone()[0]=='test-only-existing-hash'
        assert c.execute('select awarded_points from user_solves').fetchone()[0]==100
        assert c.execute('select flag_id, challenge_id, template_id from challenge_flags').fetchone()==(1,1,1)
        assert c.execute('select challenge_template_id,challenge_id,template_id,vm_role from challenge_templates').fetchone()==(1,1,1,'target')
        assert c.execute('select challenge_template_id from vm_instances').fetchone()[0]==1
        assert c.execute('select execution_type from challenges').fetchone()[0]=='vm'
        assert not c.execute('pragma foreign_key_check').fetchall()
    assert migrate(backup)['state']=='old'
    assert migrate(old,apply=True)['changed'] is False


def test_confirmation_and_backup_are_required(old,tmp_path):
    before=old.read_bytes()
    with pytest.raises(MigrationError):migrate(old,apply=True,backup=tmp_path/'backup.db')
    with pytest.raises(MigrationError):migrate(old,apply=True,confirm_existing_vm=True)
    assert old.read_bytes()==before


def test_backup_never_overwrites(old,tmp_path):
    backup=tmp_path/'backup.db';backup.write_bytes(b'keep me')
    with pytest.raises(FileExistsError):migrate(old,apply=True,backup=backup,confirm_existing_vm=True)
    assert backup.read_bytes()==b'keep me'
    assert migrate(old)['state']=='old'


def test_unknown_teammate_column_is_not_dropped(old,tmp_path):
    with sqlite3.connect(old) as c:c.execute('alter table vm_templates add column teammate_data text')
    with pytest.raises(MigrationError,match='Unrecognized'):
        migrate(old,apply=True,backup=tmp_path/'backup.db',confirm_existing_vm=True)
    with sqlite3.connect(old) as c:
        assert 'challenge_id' in {row[1] for row in c.execute('pragma table_info(vm_templates)')}


def test_foreign_key_breakage_refused_without_repair(old,tmp_path):
    with sqlite3.connect(old) as c:c.execute('update challenge_flags set template_id=999')
    with pytest.raises(MigrationError,match='Foreign-key'):
        migrate(old,apply=True,backup=tmp_path/'backup.db',confirm_existing_vm=True)


def test_failed_rebuild_rolls_back_every_table(old,tmp_path):
    # Old duplicate roles cannot become unique challenge assignments.
    with sqlite3.connect(old) as c:
        c.execute('insert into vm_templates select 2,challenge_id,template_name,1201,proxmox_node,snapshot_name,vm_role,cpu_cores,memory_mb,disk_gb,static_ip,boot_order,hostname_prefix,network_name,is_user_accessible,is_active,created_at from vm_templates where template_id=1')
    with pytest.raises(sqlite3.IntegrityError):
        migrate(old,apply=True,backup=tmp_path/'backup.db',confirm_existing_vm=True)
    assert migrate(old)['state']=='old'
    with sqlite3.connect(old) as c:assert c.execute('select count(*) from vm_templates').fetchone()[0]==2
