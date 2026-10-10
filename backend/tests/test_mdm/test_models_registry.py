import subprocess
import sys


def test_mdm_models_register_users_table_for_their_foreign_keys():
    """Import jobs run in a fresh spawned process that imports only what it needs.

    Resolving the users foreign key there used to raise NoReferencedTableError, so the
    job could not even record its failure. Checked in a fresh interpreter, not the test process.
    """
    script = ("from masterone.mdm.models import ResolutionUploadSession as S\n"
              "print(list(S.__table__.c.owner_user_id.foreign_keys)[0].column.table.name)\n")
    result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "users"
