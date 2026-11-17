import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import patch_proton_nm as p

mock_dir = Path("/usr/lib/python3/dist-packages/proton/vpn/backend/networkmanager/killswitch/default")
subprocess.run(["sudo", "mkdir", "-p", str(mock_dir)], check=True)

var = p.VARIANTS.pop(0)
old_add = p.OLD_ADD_SECTION.replace("{add_err_prefix}", var["add_err_prefix"]).replace("{add_err_arg}", var["add_err_arg"])
old_rem = p.OLD_REMOVE_SECTION.replace("{rem_err_prefix}", var["rem_err_prefix"]).replace("{rem_err_arg}", var["rem_err_arg"])

mock_content = (
    "class NMClient:\n"
    "    def add_connection_async(self):\n"
    f"{old_add}\n\n"
    "    def remove_connection_async(self):\n"
    f"{old_rem}\n"
)

mock_file = mock_dir / "nmclient.py"
subprocess.run(["sudo", "tee", str(mock_file)], input=mock_content.encode("utf-8"), stdout=subprocess.DEVNULL, check=True)

subprocess.run([sys.executable, "patch_proton_nm.py", "--dry-run"], check=True)
subprocess.run(["sudo", sys.executable, "patch_proton_nm.py"], check=True)
assert p.PATCH_MARKER in mock_file.read_text(encoding="utf-8")
subprocess.run(["sudo", sys.executable, "patch_proton_nm.py"], check=True)
subprocess.run(["sudo", sys.executable, "patch_proton_nm.py", "--restore"], check=True)
assert mock_file.read_text(encoding="utf-8") == mock_content
print("All CI simulation checks passed!")

