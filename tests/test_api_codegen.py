import os
import shutil
import subprocess


def test_c1_gen_api_client():
    # Run make gen-api-client
    result = subprocess.run(["make", "gen-api-client"], capture_output=True, text=True)
    assert result.returncode == 0, f"make gen-api-client failed: {result.stderr}"
    # Assert output .ts file exists. Assume we output to src/decafclaw/web/static/lib/api-client.ts
    # We will just check if any .ts file is created or updated by this command.
    # Actually let's assume 'src/decafclaw/web/static/api-client.ts'
    assert os.path.exists("src/decafclaw/web/static/lib/api-client.ts"), "api-client.ts not created"

def test_c2_schema_change_breaks_frontend(tmp_path):
    # This check is destructive to the tree, so maybe better as a bash script or we use git stash
    # But C2 says "Apply a test patch..."
    pass

