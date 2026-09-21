"""First-run setup for run.sh. Privileged operations never run the app as root."""
from __future__ import annotations

import ctypes.util
import grp
import os
from pathlib import Path
import pwd
import shlex
import shutil
import struct
import subprocess
import sys
import tempfile

PROJECT_DIR = Path(__file__).resolve().parent.parent
PYTHON = PROJECT_DIR / ".venv/bin/python"


def run(command: list[str]) -> None:
    subprocess.run(command, check=True)


def privileged(command: list[str], *, as_user: str | None = None) -> None:
    if not shutil.which("sudo"):
        raise RuntimeError("Setup needs sudo. Ask your administrator to run: "
                           + shlex.join(command))
    if not sys.stdin.isatty():
        raise RuntimeError("One-time system setup needs an interactive terminal. "
                           "Run ./run.sh --setup in a terminal first.")
    options = ["-u", as_user, "-g", "input", "--preserve-env"] if as_user else []
    run(["sudo", *options, "--", *command])


def missing_system_dependencies() -> list[str]:
    missing = []
    if not shutil.which("mpv"):
        missing.append("mpv")
    for library in ("portaudio", "sndfile", "pulse"):
        if not ctypes.util.find_library(library):
            missing.append(library)
    # evdev may need a source build on free-threaded Python.
    if not shutil.which("cc"):
        missing.append("compiler")
    if not Path("/usr/include/linux/input.h").exists():
        missing.append("input_headers")
    return missing


def ensure_system_dependencies() -> None:
    missing = missing_system_dependencies()
    if not missing:
        return
    if shutil.which("pacman"):
        packages = {"mpv": "mpv", "portaudio": "portaudio", "sndfile": "libsndfile",
                    "pulse": "libpulse", "compiler": "base-devel",
                    "input_headers": "linux-api-headers"}
        install = ["pacman", "-S", "--needed", "--noconfirm"]
    elif shutil.which("apt-get"):
        packages = {"mpv": "mpv", "portaudio": "libportaudio2", "sndfile": "libsndfile1",
                    "pulse": "libpulse0", "compiler": "build-essential",
                    "input_headers": "linux-libc-dev"}
        install = ["apt-get", "install", "-y"]
    else:
        raise RuntimeError("Install these system dependencies with your package manager, "
                           "then rerun ./run.sh: " + ", ".join(missing))
    print("Installing missing system dependencies: " + ", ".join(missing), flush=True)
    privileged([*install, *(packages[name] for name in missing)])
    remaining = missing_system_dependencies()
    if remaining:
        raise RuntimeError("System dependencies still missing: " + ", ".join(remaining))


def keyboard_paths() -> list[Path]:
    """Identify keyboards without reading any key events or needing device access."""
    paths = []
    # Linux KEY_A, KEY_Z, KEY_SPACE, KEY_ENTER, also used by KeyboardMonitor.
    required_codes = (30, 44, 57, 28)
    for entry in sorted(Path("/sys/class/input").glob("event*")):
        try:
            bits = 0
            for word in (entry / "device/capabilities/key").read_text().split():
                bits = (bits << (struct.calcsize("L") * 8)) | int(word, 16)
        except (OSError, ValueError):
            continue
        if all(bits & (1 << code) for code in required_codes):
            paths.append(Path("/dev/input") / entry.name)
    return paths


def unreadable_keyboards(paths: list[Path]) -> list[Path]:
    denied = []
    for path in paths:
        try:
            fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
        except PermissionError:
            denied.append(path)
        except FileNotFoundError:
            continue  # Unplugged during the check.
        else:
            os.close(fd)
    return denied


def ensure_input_group(paths: list[Path]) -> grp.struct_group:
    try:
        input_group = grp.getgrnam("input")
    except KeyError:
        print("Creating the input group for keyboard access...", flush=True)
        privileged(["groupadd", "--system", "input"])
        input_group = grp.getgrnam("input")
    if any(path.stat().st_gid != input_group.gr_gid for path in paths):
        if not shutil.which("udevadm"):
            raise RuntimeError("Install udev to configure persistent keyboard permissions.")
        rule = ('SUBSYSTEM=="input", KERNEL=="event*", ENV{ID_INPUT_KEYBOARD}=="1", '
                'GROUP="input", MODE="0660"\n')
        print("Installing a udev rule to give the input group keyboard access...", flush=True)
        with tempfile.NamedTemporaryFile(mode="w", suffix=".rules") as temporary:
            temporary.write(rule)
            temporary.flush()
            privileged(["install", "-D", "-m", "0644", temporary.name,
                        "/etc/udev/rules.d/99-bgmusic-input.rules"])
        privileged(["udevadm", "control", "--reload-rules"])
        for path in paths:
            privileged(["udevadm", "trigger", "--action=change",
                        str(Path("/sys/class/input") / path.name)])
        privileged(["udevadm", "settle"])
    return input_group


def ensure_keyboard_access(relaunch: list[str]) -> None:
    paths = keyboard_paths()
    if not paths:
        raise RuntimeError("No keyboard devices found. Connect a keyboard and rerun ./run.sh.")
    denied = unreadable_keyboards(paths)
    if not denied:
        return
    input_group = ensure_input_group(denied)
    if not unreadable_keyboards(paths):
        return
    if input_group.gr_gid in {os.getgid(), *os.getgroups()}:
        raise RuntimeError("The input group is active but keyboard access is still denied: "
                           + ", ".join(map(str, denied)))
    account = pwd.getpwuid(os.getuid())
    if input_group.gr_gid not in os.getgrouplist(account.pw_name, account.pw_gid):
        print("Global hotkeys and keyboard sounds need raw keyboard access.\n"
              f"Adding {account.pw_name} to the input group (allows reading input devices).",
              flush=True)
        privileged(["usermod", "-aG", "input", account.pw_name])
    # Refresh the app's groups immediately; the desktop session can stay logged in.
    # The target UID is the current user, never root. Preserve the audio/Wayland session.
    print("Starting with the input group active. A full logout/login will make this\n"
          "group available to future terminals without sudo.", flush=True)
    privileged(relaunch, as_user=account.pw_name)
    raise SystemExit(0)


def check_session() -> None:
    import pulsectl

    try:
        with pulsectl.Pulse("bgmusic-setup"):
            pass
    except Exception as error:
        raise RuntimeError("Cannot connect to desktop audio. Start PulseAudio or "
                           "PipeWire with pipewire-pulse, then rerun ./run.sh. "
                           f"Details: {error}") from error


def main() -> None:
    uv, *arguments = sys.argv[1:]
    if os.geteuid() == 0:
        raise RuntimeError("Run ./run.sh as your normal user, not root.")
    ensure_system_dependencies()
    # uv audits satisfied requirements quickly and installs newly added dependencies.
    # No marker file: interrupted setups and requirements changes are retried naturally.
    run([uv, "pip", "install", "--python", str(PYTHON),
         "-r", str(PROJECT_DIR / "requirements.txt"), "--quiet"])
    sys.path.insert(0, str(PROJECT_DIR))
    from bgmusic.cli import build_parser
    from bgmusic.config import bool_setting, load_config

    parser = build_parser()
    parser.prog = "./run.sh"
    parser.add_argument("--setup", action="store_true",
                        help="Prepare dependencies and permissions without starting playback")
    args = parser.parse_args(arguments)
    if args.setup or args.action in {None, "start"}:
        config = load_config(Path(args.config))
        if config.get("hotkeys") or bool_setting(config["keyboard_sounds"].get("enabled"), True):
            ensure_keyboard_access([str(PYTHON), "-B", str(Path(__file__).resolve()),
                                    uv, *arguments])
        check_session()
        if args.setup:
            print("Setup complete. Run ./run.sh to start music.")
            return
    os.execv(str(PYTHON), [str(PYTHON), str(PROJECT_DIR / "bgmusic.py"), *arguments])


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, OSError, subprocess.CalledProcessError) as error:
        print(f"Setup failed: {error}", file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        sys.exit(130)
