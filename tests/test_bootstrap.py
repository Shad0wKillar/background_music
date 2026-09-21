from __future__ import annotations

import contextlib
import io
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from bgmusic import bootstrap


class KeyboardSetupTests(unittest.TestCase):
    def setUp(self) -> None:
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
        self.paths = [Path("/dev/input/event9")]
        self.stack.enter_context(patch.object(bootstrap, "keyboard_paths", return_value=self.paths))
        self.access = self.stack.enter_context(
            patch.object(bootstrap, "unreadable_keyboards", return_value=self.paths))
        self.group = self.stack.enter_context(
            patch.object(bootstrap, "ensure_input_group", return_value=SimpleNamespace(gr_gid=992)))
        self.privileged = self.stack.enter_context(patch.object(bootstrap, "privileged"))
        self.stack.enter_context(patch.object(bootstrap.os, "getgid", return_value=1000))
        self.groups = self.stack.enter_context(patch.object(bootstrap.os, "getgroups", return_value=[1000]))
        self.membership = self.stack.enter_context(
            patch.object(bootstrap.os, "getgrouplist", return_value=[1000]))
        self.stack.enter_context(patch.object(bootstrap.pwd, "getpwuid",
                                            return_value=SimpleNamespace(pw_name="listener", pw_gid=1000)))
        self.command = ["/tmp/project with spaces/.venv/bin/python", "bootstrap.py", "--setup"]

    def test_readable_keyboard_needs_no_system_changes(self) -> None:
        self.access.return_value = []
        bootstrap.ensure_keyboard_access(self.command)
        self.group.assert_not_called()
        self.privileged.assert_not_called()

    def test_new_user_is_added_then_relaunched_as_same_user(self) -> None:
        with self.assertRaises(SystemExit) as exited:
            bootstrap.ensure_keyboard_access(self.command)
        self.assertEqual(exited.exception.code, 0)
        self.assertEqual(self.privileged.call_args_list, [
            unittest.mock.call(["usermod", "-aG", "input", "listener"]),
            unittest.mock.call(self.command, as_user="listener"),
        ])

    def test_existing_membership_is_not_added_again(self) -> None:
        self.membership.return_value = [1000, 992]
        with self.assertRaises(SystemExit):
            bootstrap.ensure_keyboard_access(self.command)
        self.privileged.assert_called_once_with(self.command, as_user="listener")

    def test_denied_access_with_active_group_does_not_relaunch_forever(self) -> None:
        self.groups.return_value = [1000, 992]
        with self.assertRaisesRegex(RuntimeError, "still denied"):
            bootstrap.ensure_keyboard_access(self.command)
        self.privileged.assert_not_called()

    def test_failed_group_change_does_not_launch_app(self) -> None:
        self.privileged.side_effect = RuntimeError("sudo failed")
        with self.assertRaisesRegex(RuntimeError, "sudo failed"):
            bootstrap.ensure_keyboard_access(self.command)
        self.privileged.assert_called_once_with(["usermod", "-aG", "input", "listener"])

    def test_no_keyboard_does_not_change_groups(self) -> None:
        with patch.object(bootstrap, "keyboard_paths", return_value=[]):
            with self.assertRaisesRegex(RuntimeError, "No keyboard"):
                bootstrap.ensure_keyboard_access(self.command)
        self.privileged.assert_not_called()


class SystemSetupTests(unittest.TestCase):
    def test_satisfied_dependencies_do_not_request_sudo(self) -> None:
        with patch.object(bootstrap, "missing_system_dependencies", return_value=[]), \
                patch.object(bootstrap, "privileged") as privileged:
            bootstrap.ensure_system_dependencies()
        privileged.assert_not_called()

    def test_only_missing_native_packages_are_installed(self) -> None:
        with patch.object(bootstrap, "missing_system_dependencies", side_effect=[["mpv"], []]), \
                patch.object(bootstrap.shutil, "which", return_value="/usr/bin/pacman"), \
                patch.object(bootstrap, "privileged") as privileged, \
                contextlib.redirect_stdout(io.StringIO()):
            bootstrap.ensure_system_dependencies()
        privileged.assert_called_once_with(["pacman", "-S", "--needed", "--noconfirm", "mpv"])

    def test_noninteractive_setup_fails_before_sudo(self) -> None:
        with patch.object(bootstrap.shutil, "which", return_value="/usr/bin/sudo"), \
                patch.object(bootstrap.sys.stdin, "isatty", return_value=False), \
                patch.object(bootstrap, "run") as run:
            with self.assertRaisesRegex(RuntimeError, "interactive terminal"):
                bootstrap.privileged(["usermod", "-aG", "input", "listener"])
        run.assert_not_called()

    def test_group_refresh_preserves_session_and_never_targets_root(self) -> None:
        with patch.object(bootstrap.shutil, "which", return_value="/usr/bin/sudo"), \
                patch.object(bootstrap.sys.stdin, "isatty", return_value=True), \
                patch.object(bootstrap, "run") as run:
            bootstrap.privileged(["/project path/python", "--setup"], as_user="listener")
        run.assert_called_once_with(["sudo", "-u", "listener", "-g", "input",
                                     "--preserve-env", "--", "/project path/python", "--setup"])

    def test_missing_input_group_is_created(self) -> None:
        group = SimpleNamespace(gr_gid=992)
        path = Mock()
        path.stat.return_value.st_gid = 992
        with patch.object(bootstrap.grp, "getgrnam", side_effect=[KeyError("input"), group]), \
                patch.object(bootstrap, "privileged") as privileged, \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertIs(bootstrap.ensure_input_group([path]), group)
        privileged.assert_called_once_with(["groupadd", "--system", "input"])


if __name__ == "__main__":
    unittest.main()
