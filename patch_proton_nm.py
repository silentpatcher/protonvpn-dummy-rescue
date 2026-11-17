#!/usr/bin/env python3
"""Proton VPN Linux NetworkManager Kill Switch Fix (v4.2)."""

import argparse
import os
import py_compile
import shutil
import subprocess
import sys
from pathlib import Path

PATCH_MARKER = "# [protonvpn-dummy-rescue v4.2]"
BASE_SEARCH_DIRS = [
    Path("/usr/lib/python3/dist-packages/proton/vpn"),
    *sorted(Path("/usr/lib").glob("python3.*/site-packages/proton/vpn")),
]

OLD_ADD_SECTION = """        def _on_connection_added(nm_client, res, _user_data):
            try:
                # Make sure exceptions creating the connection are passed to the future.
                nm_client.add_connection_finish(res)
            except Exception as exc:  # pylint: disable=broad-except
                future_conn_activated.set_exception(
                    RuntimeError(
                        f"{add_err_prefix}: {add_err_arg}"
                    ).with_traceback(exc.__traceback__)
                )
                return

        def _add_connection_async():
            # Set up interface connection monitoring, which resolves the future
            # once the kill switch is active.
            handler_id = self._nm_client.connect("device-added", _on_interface_added)
            future_conn_activated.add_done_callback(
                lambda f: self._run_on_glib_loop_thread(
                    GObject.signal_handler_disconnect, self._nm_client, handler_id
                ).result()
            )

            # Add kill switch connection asynchronously.
            self._nm_client.add_connection_async(
                connection=connection,
                save_to_disk=save_to_disk,
                cancellable=None,
                callback=_on_connection_added,
                user_data=None
            )"""

NEW_ADD_SECTION = """        def _on_connection_added(nm_client, res, _user_data):
            # [protonvpn-dummy-rescue v4.2]
            try:
                nm_client.add_connection_finish(res)
                if not future_conn_activated.done():
                    future_conn_activated.set_result(None)
            except Exception as exc:  # pylint: disable=broad-except
                if not future_conn_activated.done():
                    future_conn_activated.set_exception(
                        RuntimeError(
                            f"{add_err_prefix}: {add_err_arg}"
                        ).with_traceback(exc.__traceback__)
                    )

        def _add_connection_async():
            # Add kill switch connection asynchronously.
            self._nm_client.add_connection_async(
                connection=connection,
                save_to_disk=save_to_disk,
                cancellable=None,
                callback=_on_connection_added,
                user_data=None
            )"""


OLD_REMOVE_SECTION = """        def _on_connection_removed(connection, result, _user_data):
            try:
                connection.delete_finish(result)
            except Exception as exc:  # pylint: disable=broad-except
                future_interface_removed.set_exception(
                    RuntimeError(
                        f"{rem_err_prefix}: {rem_err_arg}"
                    ).with_traceback(exc.__traceback__)
                )

        def _on_interface_removed(_nm_client, device):
            logger.debug(
                f"{device.get_iface()} was removed."
            )
            if device.get_iface() == connection.get_interface_name():
                future_interface_removed.set_result(None)

        def _remove_connection_async():
            handler_id = self._nm_client.connect("device-removed", _on_interface_removed)
            future_interface_removed.add_done_callback(
                lambda f: self._run_on_glib_loop_thread(
                    GObject.signal_handler_disconnect, self._nm_client, handler_id
                ).result()
            )

            connection.delete_async(
                None,
                _on_connection_removed,
                None
            )"""

NEW_REMOVE_SECTION = """        def _on_connection_removed(connection, result, _user_data):
            # [protonvpn-dummy-rescue v4.2]
            try:
                connection.delete_finish(result)
                if not future_interface_removed.done():
                    future_interface_removed.set_result(None)
            except Exception as exc:  # pylint: disable=broad-except
                if "connection was deleted" in str(exc).lower():
                    logger.debug("Connection was already deleted in NetworkManager.")
                    if not future_interface_removed.done():
                        future_interface_removed.set_result(None)
                    return
                if not future_interface_removed.done():
                    future_interface_removed.set_exception(
                        RuntimeError(
                            f"{rem_err_prefix}: {rem_err_arg}"
                        ).with_traceback(exc.__traceback__)
                    )

        def _remove_connection_async():
            connection.delete_async(
                None,
                _on_connection_removed,
                None
            )"""


VARIANTS = [
    {
        "add_err_prefix": "Error setting adding KS connection",
        "add_err_arg": "{nm_client=}, {res=}",
        "rem_err_prefix": "Error removing KS connection",
        "rem_err_arg": "{connection=}, {result=}",
    },
    {
        "add_err_prefix": "Error adding KS connection",
        "add_err_arg": "{exc}",
        "rem_err_prefix": "Error removing KS connection",
        "rem_err_arg": "{exc}",
    },
]


def is_protonvpn_running() -> bool:
    res = subprocess.run(["pgrep", "-f", "protonvpn-app"], capture_output=True, text=True)
    return res.returncode == 0


def discover_target_files(base_dirs: list[Path]) -> list[Path]:
    targets = []
    for base_dir in base_dirs:
        if not base_dir.exists():
            continue
        for path in sorted(base_dir.rglob("nmclient.py")):
            if path.parent.name != "core" and path not in targets:
                targets.append(path)
    return targets


def restore_files(targets: list[Path], dry_run: bool) -> bool:
    for filepath in targets:
        backup_path = filepath.with_suffix(".py.bak")
        if not backup_path.exists():
            print(f"[SKIP] No backup found for: {filepath}")
            continue
        if dry_run:
            print(f"[DRY-RUN] Would restore {backup_path} -> {filepath}")
            continue
        shutil.copy2(backup_path, filepath)
        py_compile.compile(str(filepath), doraise=True)
        print(f"[RESTORED] {filepath} from backup {backup_path}")
    return True


def patch_file(filepath: Path, dry_run: bool) -> bool:
    if not filepath.exists():
        print(f"[ERROR] File not found: {filepath}")
        return False

    content = filepath.read_text(encoding="utf-8")

    if PATCH_MARKER in content:
        print(f"[OK] Already patched (v4.2): {filepath}")
        return True

    matched_variant = None
    for var in VARIANTS:
        old_add = OLD_ADD_SECTION.replace("{add_err_prefix}", var["add_err_prefix"]).replace("{add_err_arg}", var["add_err_arg"])
        old_rem = OLD_REMOVE_SECTION.replace("{rem_err_prefix}", var["rem_err_prefix"]).replace("{rem_err_arg}", var["rem_err_arg"])
        if old_add in content and old_rem in content:
            matched_variant = (var, old_add, old_rem)
            break

    if matched_variant is None:
        print(f"[ERROR] Target code blocks did not match in: {filepath}")
        return False

    var, old_add, old_rem = matched_variant
    new_add = NEW_ADD_SECTION.replace("{add_err_prefix}", var["add_err_prefix"]).replace("{add_err_arg}", var["add_err_arg"])
    new_rem = NEW_REMOVE_SECTION.replace("{rem_err_prefix}", var["rem_err_prefix"]).replace("{rem_err_arg}", var["rem_err_arg"])

    if dry_run:
        print(f"[DRY-RUN] Would apply patch v4.2 to: {filepath}")
        return True

    backup_path = filepath.with_suffix(".py.bak")
    if not backup_path.exists():
        shutil.copy2(filepath, backup_path)
        print(f"[BACKUP] Created backup: {backup_path}")

    patched_content = content.replace(old_add, new_add, 1).replace(old_rem, new_rem, 1)

    filepath.write_text(patched_content, encoding="utf-8")
    py_compile.compile(str(filepath), doraise=True)
    print(f"[SUCCESS] Patch v4.2 applied and compiled on: {filepath}")
    return True


def main():
    parser = argparse.ArgumentParser(description="Patch Proton VPN NetworkManager Kill Switch async callbacks.")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be done without modifying files.")
    parser.add_argument("--restore", action="store_true", help="Restore original files from .py.bak backups.")
    args = parser.parse_args()

    if not args.dry_run and os.geteuid() != 0:
        print("[ERROR] Run this script with sudo (e.g. sudo python3 ~/patch_proton_nm.py).")
        sys.exit(1)

    if not args.dry_run and is_protonvpn_running():
        print("[ERROR] Completely quit the Proton VPN app before applying or restoring the patch.")
        sys.exit(1)

    targets = discover_target_files(BASE_SEARCH_DIRS)
    if not targets:
        print(f"[ERROR] No target nmclient.py files found under {BASE_SEARCH_DIRS}")
        sys.exit(1)

    print(f"[INFO] Found {len(targets)} target files:")
    for t in targets:
        print(f"  - {t}")

    if args.restore:
        ok = restore_files(targets, args.dry_run)
        sys.exit(0 if ok else 1)

    all_ok = True
    for target in targets:
        if not patch_file(target, args.dry_run):
            all_ok = False
    sys.exit(0 if all_ok else 1)


if __name__ == "__main__":
    main()


