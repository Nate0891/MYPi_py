#!/usr/bin/env python3
"""
adb_fastboot.py - A small Python wrapper and CLI around adb and fastboot.

Requirements:
    - Python 3.8+
    - Android platform-tools (adb, fastboot) installed and on your PATH,
      or pass --platform-tools /path/to/platform-tools

Use as a library:
    from adb_fastboot import ADB, Fastboot
    adb = ADB()
    print(adb.devices())
    adb.install("app.apk")

Use as a CLI:
    python adb_fastboot.py adb devices
    python adb_fastboot.py adb shell "ls /sdcard"
    python adb_fastboot.py adb install app.apk
    python adb_fastboot.py adb screenshot shot.png
    python adb_fastboot.py fastboot devices
    python adb_fastboot.py fastboot flash boot boot.img
"""

import argparse
import shlex
import shutil
import subprocess
import sys
from pathlib import Path
from typing import List, Optional


class ToolError(RuntimeError):
    """Raised when adb/fastboot is missing or a command fails."""


class _Tool:
    """Base class that locates and runs a platform-tools binary."""

    binary = ""

    def __init__(self, serial: Optional[str] = None, platform_tools: Optional[str] = None):
        self.serial = serial
        self.path = self._locate(platform_tools)

    def _locate(self, platform_tools: Optional[str]) -> str:
        if platform_tools:
            candidate = Path(platform_tools) / self.binary
            for p in (candidate, candidate.with_suffix(".exe")):
                if p.exists():
                    return str(p)
        found = shutil.which(self.binary)
        if not found:
            raise ToolError(
                f"'{self.binary}' not found. Install Android platform-tools and add it "
                f"to your PATH, or pass --platform-tools."
            )
        return found

    def _base_cmd(self) -> List[str]:
        cmd = [self.path]
        if self.serial:
            cmd += ["-s", self.serial]
        return cmd

    def run(self, *args: str, timeout: Optional[int] = 120, check: bool = True) -> str:
        """Run a command and return combined stdout/stderr as text."""
        cmd = self._base_cmd() + list(args)
        try:
            proc = subprocess.run(
                cmd, capture_output=True, text=True, timeout=timeout
            )
        except subprocess.TimeoutExpired:
            raise ToolError(f"Timed out: {' '.join(cmd)}")
        # fastboot writes most output to stderr, so merge both streams
        output = (proc.stdout + proc.stderr).strip()
        if check and proc.returncode != 0:
            raise ToolError(f"Command failed ({proc.returncode}): {' '.join(cmd)}\n{output}")
        return output

    def stream(self, *args: str) -> int:
        """Run a command and stream its output live (Ctrl+C to stop)."""
        cmd = self._base_cmd() + list(args)
        try:
            return subprocess.call(cmd)
        except KeyboardInterrupt:
            return 130


class ADB(_Tool):
    binary = "adb"

    def start_server(self) -> str:
        return self.run("start-server")

    def kill_server(self) -> str:
        return self.run("kill-server")

    def version(self) -> str:
        return self.run("version")

    def devices(self) -> List[str]:
        """Return a list of 'serial<TAB>state' lines."""
        out = self.run("devices", "-l")
        return [line for line in out.splitlines()[1:] if line.strip() and not line.startswith("*")]

    def shell(self, command: str, timeout: Optional[int] = 120) -> str:
        return self.run("shell", command, timeout=timeout)

    def install(self, apk: str, replace: bool = True, downgrade: bool = False) -> str:
        args = ["install"]
        if replace:
            args.append("-r")
        if downgrade:
            args.append("-d")
        args.append(apk)
        return self.run(*args, timeout=600)

    def uninstall(self, package: str, keep_data: bool = False) -> str:
        args = ["uninstall"]
        if keep_data:
            args.append("-k")
        args.append(package)
        return self.run(*args)

    def list_packages(self, filter_text: str = "", third_party: bool = False) -> List[str]:
        args = ["shell", "pm", "list", "packages"]
        if third_party:
            args.append("-3")
        out = self.run(*args)
        pkgs = [l.replace("package:", "") for l in out.splitlines() if l.startswith("package:")]
        return [p for p in pkgs if filter_text in p]

    def push(self, local: str, remote: str) -> str:
        return self.run("push", local, remote, timeout=None)

    def pull(self, remote: str, local: str = ".") -> str:
        return self.run("pull", remote, local, timeout=None)

    def screenshot(self, out_path: str = "screenshot.png") -> str:
        cmd = self._base_cmd() + ["exec-out", "screencap", "-p"]
        proc = subprocess.run(cmd, capture_output=True, timeout=60)
        if proc.returncode != 0:
            raise ToolError(proc.stderr.decode(errors="replace"))
        Path(out_path).write_bytes(proc.stdout)
        return out_path

    def logcat(self, *extra: str) -> int:
        """Stream logcat. Example: adb.logcat('*:E')"""
        return self.stream("logcat", *extra)

    def clear_logcat(self) -> str:
        return self.run("logcat", "-c")

    def reboot(self, mode: str = "") -> str:
        """mode: '', 'bootloader', 'recovery', 'sideload', 'fastboot'"""
        return self.run("reboot", mode) if mode else self.run("reboot")

    def sideload(self, zip_path: str) -> str:
        return self.run("sideload", zip_path, timeout=None)

    def get_prop(self, prop: str) -> str:
        return self.shell(f"getprop {prop}")

    def device_info(self) -> dict:
        props = {
            "model": "ro.product.model",
            "manufacturer": "ro.product.manufacturer",
            "android_version": "ro.build.version.release",
            "sdk": "ro.build.version.sdk",
            "build": "ro.build.display.id",
            "abi": "ro.product.cpu.abi",
        }
        return {k: self.get_prop(v) for k, v in props.items()}

    def tcpip(self, port: int = 5555) -> str:
        return self.run("tcpip", str(port))

    def connect(self, host: str) -> str:
        return self.run("connect", host)

    def disconnect(self, host: str = "") -> str:
        return self.run("disconnect", host) if host else self.run("disconnect")

    def tap(self, x: int, y: int) -> str:
        return self.shell(f"input tap {x} {y}")

    def swipe(self, x1: int, y1: int, x2: int, y2: int, ms: int = 300) -> str:
        return self.shell(f"input swipe {x1} {y1} {x2} {y2} {ms}")

    def text(self, value: str) -> str:
        return self.shell("input text " + shlex.quote(value))

    def keyevent(self, key: str) -> str:
        return self.shell(f"input keyevent {key}")


class Fastboot(_Tool):
    binary = "fastboot"

    def devices(self) -> List[str]:
        out = self.run("devices")
        return [l for l in out.splitlines() if l.strip()]

    def getvar(self, name: str = "all") -> str:
        return self.run("getvar", name)

    def flash(self, partition: str, image: str) -> str:
        return self.run("flash", partition, image, timeout=None)

    def erase(self, partition: str) -> str:
        return self.run("erase", partition)

    def boot(self, image: str) -> str:
        """Boot an image temporarily without flashing it."""
        return self.run("boot", image, timeout=None)

    def set_active(self, slot: str) -> str:
        return self.run("set_active", slot)

    def reboot(self, mode: str = "") -> str:
        """mode: '', 'bootloader', 'recovery', 'fastboot'"""
        return self.run("reboot", mode) if mode else self.run("reboot")

    def oem_unlock(self) -> str:
        """Modern devices: 'flashing unlock'. WIPES THE DEVICE."""
        return self.run("flashing", "unlock")

    def oem_lock(self) -> str:
        """Modern devices: 'flashing lock'. WIPES THE DEVICE."""
        return self.run("flashing", "lock")

    def wipe_data(self) -> str:
        return self.run("-w", timeout=None)


def _confirm(message: str) -> None:
    answer = input(f"WARNING: {message}\nType 'yes' to continue: ").strip().lower()
    if answer != "yes":
        print("Cancelled.")
        sys.exit(1)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Python wrapper for adb and fastboot")
    p.add_argument("-s", "--serial", help="Target a specific device serial")
    p.add_argument("--platform-tools", help="Path to the platform-tools folder")
    tools = p.add_subparsers(dest="tool", required=True)

    # ---- adb ----
    a = tools.add_parser("adb", help="adb commands").add_subparsers(dest="cmd", required=True)
    a.add_parser("devices")
    a.add_parser("info", help="Show basic device info")
    a.add_parser("start-server")
    a.add_parser("kill-server")
    s = a.add_parser("shell"); s.add_argument("command")
    i = a.add_parser("install"); i.add_argument("apk")
    u = a.add_parser("uninstall"); u.add_argument("package")
    l = a.add_parser("packages"); l.add_argument("filter", nargs="?", default="")
    l.add_argument("-3", "--third-party", action="store_true")
    pu = a.add_parser("push"); pu.add_argument("local"); pu.add_argument("remote")
    pl = a.add_parser("pull"); pl.add_argument("remote"); pl.add_argument("local", nargs="?", default=".")
    sc = a.add_parser("screenshot"); sc.add_argument("out", nargs="?", default="screenshot.png")
    lc = a.add_parser("logcat"); lc.add_argument("args", nargs="*")
    rb = a.add_parser("reboot")
    rb.add_argument("mode", nargs="?", default="",
                    choices=["", "bootloader", "recovery", "sideload", "fastboot"])
    sl = a.add_parser("sideload"); sl.add_argument("zip")
    co = a.add_parser("connect"); co.add_argument("host", help="ip:port")
    a.add_parser("tcpip")

    # ---- fastboot ----
    f = tools.add_parser("fastboot", help="fastboot commands").add_subparsers(dest="cmd", required=True)
    f.add_parser("devices")
    gv = f.add_parser("getvar"); gv.add_argument("name", nargs="?", default="all")
    fl = f.add_parser("flash"); fl.add_argument("partition"); fl.add_argument("image")
    er = f.add_parser("erase"); er.add_argument("partition")
    bt = f.add_parser("boot"); bt.add_argument("image")
    sa = f.add_parser("set-active"); sa.add_argument("slot", choices=["a", "b"])
    frb = f.add_parser("reboot")
    frb.add_argument("mode", nargs="?", default="", choices=["", "bootloader", "recovery", "fastboot"])
    f.add_parser("unlock")
    f.add_parser("lock")
    f.add_parser("wipe")
    return p


def main() -> int:
    args = build_parser().parse_args()
    try:
        if args.tool == "adb":
            t = ADB(args.serial, args.platform_tools)
            c = args.cmd
            if c == "devices":
                print("\n".join(t.devices()) or "No devices found.")
            elif c == "info":
                for k, v in t.device_info().items():
                    print(f"{k:16}{v}")
            elif c == "start-server": print(t.start_server())
            elif c == "kill-server": print(t.kill_server())
            elif c == "shell": print(t.shell(args.command))
            elif c == "install": print(t.install(args.apk))
            elif c == "uninstall": print(t.uninstall(args.package))
            elif c == "packages": print("\n".join(t.list_packages(args.filter, args.third_party)))
            elif c == "push": print(t.push(args.local, args.remote))
            elif c == "pull": print(t.pull(args.remote, args.local))
            elif c == "screenshot": print(f"Saved to {t.screenshot(args.out)}")
            elif c == "logcat": return t.logcat(*args.args)
            elif c == "reboot": print(t.reboot(args.mode) or "Rebooting...")
            elif c == "sideload": print(t.sideload(args.zip))
            elif c == "connect": print(t.connect(args.host))
            elif c == "tcpip": print(t.tcpip())
        else:
            t = Fastboot(args.serial, args.platform_tools)
            c = args.cmd
            if c == "devices":
                print("\n".join(t.devices()) or "No devices found.")
            elif c == "getvar": print(t.getvar(args.name))
            elif c == "flash":
                _confirm(f"Flashing '{args.partition}' overwrites it and can brick your device if wrong.")
                print(t.flash(args.partition, args.image))
            elif c == "erase":
                _confirm(f"Erasing '{args.partition}' is irreversible.")
                print(t.erase(args.partition))
            elif c == "boot": print(t.boot(args.image))
            elif c == "set-active": print(t.set_active(args.slot))
            elif c == "reboot": print(t.reboot(args.mode) or "Rebooting...")
            elif c == "unlock":
                _confirm("Unlocking the bootloader WIPES ALL DATA and may void your warranty.")
                print(t.oem_unlock())
            elif c == "lock":
                _confirm("Locking the bootloader WIPES ALL DATA. Only do this on stock firmware.")
                print(t.oem_lock())
            elif c == "wipe":
                _confirm("This WIPES ALL USER DATA.")
                print(t.wipe_data())
    except ToolError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
