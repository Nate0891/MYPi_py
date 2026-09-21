#!/usr/bin/env python3
"""Small Python wrapper and CLI around adb and fastboot."""

import argparse
import shlex
import shutil
import subprocess
import sys
from pathlib import Path
from typing import List, Optional, Sequence


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
            for path in (candidate, candidate.with_suffix(".exe")):
                if path.is_file():
                    return str(path)
        found = shutil.which(self.binary)
        if not found:
            raise ToolError(
                f"'{self.binary}' not found. Install Android platform-tools and add it "
                "to your PATH, or pass --platform-tools."
            )
        return found

    def _base_cmd(self) -> List[str]:
        command = [self.path]
        if self.serial:
            command += ["-s", self.serial]
        return command

    @staticmethod
    def _format_cmd(command: Sequence[str]) -> str:
        """Format a command safely for diagnostic messages."""
        return shlex.join([str(part) for part in command])

    def _run_process(
        self, command: Sequence[str], timeout: Optional[int], text: bool = True
    ) -> subprocess.CompletedProcess:
        kwargs = {
            "capture_output": True,
            "timeout": timeout,
            "check": False,
        }
        if text:
            kwargs.update({"text": True, "encoding": "utf-8", "errors": "replace"})
        try:
            return subprocess.run(list(command), **kwargs)
        except subprocess.TimeoutExpired as exc:
            limit = f" after {timeout}s" if timeout is not None else ""
            raise ToolError(f"Timed out{limit}: {self._format_cmd(command)}") from exc
        except OSError as exc:
            raise ToolError(
                f"Could not execute {self.binary}: {exc.strerror or exc}"
            ) from exc

    def run(self, *args: str, timeout: Optional[int] = 120, check: bool = True) -> str:
        """Run a command and return combined stdout/stderr as text."""
        command = self._base_cmd() + [str(arg) for arg in args]
        process = self._run_process(command, timeout)
        output = ((process.stdout or "") + (process.stderr or "")).strip()
        if check and process.returncode != 0:
            detail = f"\n{output}" if output else ""
            raise ToolError(
                f"Command failed with exit code {process.returncode}: "
                f"{self._format_cmd(command)}{detail}"
            )
        return output

    def stream(self, *args: str) -> int:
        """Run a command and stream its output live (Ctrl+C to stop)."""
        command = self._base_cmd() + [str(arg) for arg in args]
        try:
            return subprocess.call(command)
        except KeyboardInterrupt:
            return 130
        except OSError as exc:
            raise ToolError(
                f"Could not execute {self.binary}: {exc.strerror or exc}"
            ) from exc


class ADB(_Tool):
    binary = "adb"

    def start_server(self) -> str:
        return self.run("start-server")

    def kill_server(self) -> str:
        return self.run("kill-server")

    def version(self) -> str:
        return self.run("version")

    def devices(self) -> List[str]:
        """Return non-header adb device lines."""
        output = self.run("devices", "-l")
        return [line for line in output.splitlines()[1:] if line.strip() and not line.startswith("*")]

    def shell(self, command: str, timeout: Optional[int] = 120) -> str:
        return self.run("shell", command, timeout=timeout)

    def install(self, apk: str, replace: bool = True, downgrade: bool = False) -> str:
        args = ["install"]
        if replace:
            args.append("-r")
        if downgrade:
            args.append("-d")
        return self.run(*args, apk, timeout=600)

    def uninstall(self, package: str, keep_data: bool = False) -> str:
        args = ["uninstall"]
        if keep_data:
            args.append("-k")
        return self.run(*args, package)

    def list_packages(self, filter_text: str = "", third_party: bool = False) -> List[str]:
        args = ["shell", "pm", "list", "packages"]
        if third_party:
            args.append("-3")
        output = self.run(*args)
        packages = [line[len("package:"):] for line in output.splitlines() if line.startswith("package:")]
        return [package for package in packages if filter_text in package]

    def push(self, local: str, remote: str) -> str:
        return self.run("push", local, remote, timeout=None)

    def pull(self, remote: str, local: str = ".") -> str:
        return self.run("pull", remote, local, timeout=None)

    def screenshot(self, out_path: str = "screenshot.png") -> str:
        command = self._base_cmd() + ["exec-out", "screencap", "-p"]
        process = self._run_process(command, timeout=60, text=False)
        if process.returncode != 0:
            error = process.stderr.decode("utf-8", errors="replace") if process.stderr else "screenshot failed"
            raise ToolError(
                f"Command failed with exit code {process.returncode}: "
                f"{self._format_cmd(command)}\n{error.strip()}"
            )
        try:
            Path(out_path).write_bytes(process.stdout or b"")
        except OSError as exc:
            raise ToolError(f"Could not write screenshot '{out_path}': {exc}") from exc
        return out_path

    def logcat(self, *extra: str) -> int:
        return self.stream("logcat", *extra)

    def clear_logcat(self) -> str:
        return self.run("logcat", "-c")

    def reboot(self, mode: str = "") -> str:
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
        return {key: self.get_prop(value) for key, value in props.items()}

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
        return [line for line in self.run("devices").splitlines() if line.strip()]

    def getvar(self, name: str = "all") -> str:
        return self.run("getvar", name)

    def flash(self, partition: str, image: str) -> str:
        return self.run("flash", partition, image, timeout=None)

    def erase(self, partition: str) -> str:
        return self.run("erase", partition)

    def boot(self, image: str) -> str:
        return self.run("boot", image, timeout=None)

    def set_active(self, slot: str) -> str:
        return self.run("set_active", slot)

    def reboot(self, mode: str = "") -> str:
        return self.run("reboot", mode) if mode else self.run("reboot")

    def oem_unlock(self) -> str:
        return self.run("flashing", "unlock")

    def oem_lock(self) -> str:
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
    a = tools.add_parser("adb", help="adb commands").add_subparsers(dest="cmd", required=True)
    for command in ("devices", "info", "start-server", "kill-server", "tcpip"):
        a.add_parser(command)
    shell = a.add_parser("shell"); shell.add_argument("command")
    install = a.add_parser("install"); install.add_argument("apk")
    uninstall = a.add_parser("uninstall"); uninstall.add_argument("package")
    packages = a.add_parser("packages"); packages.add_argument("filter", nargs="?", default=""); packages.add_argument("-3", "--third-party", action="store_true")
    push = a.add_parser("push"); push.add_argument("local"); push.add_argument("remote")
    pull = a.add_parser("pull"); pull.add_argument("remote"); pull.add_argument("local", nargs="?", default=".")
    screenshot = a.add_parser("screenshot"); screenshot.add_argument("out", nargs="?", default="screenshot.png")
    logcat = a.add_parser("logcat"); logcat.add_argument("args", nargs="*")
    reboot = a.add_parser("reboot"); reboot.add_argument("mode", nargs="?", default="", choices=["", "bootloader", "recovery", "sideload", "fastboot"])
    sideload = a.add_parser("sideload"); sideload.add_argument("zip")
    connect = a.add_parser("connect"); connect.add_argument("host", help="ip:port")

    f = tools.add_parser("fastboot", help="fastboot commands").add_subparsers(dest="cmd", required=True)
    for command in ("devices", "unlock", "lock", "wipe"):
        f.add_parser(command)
    getvar = f.add_parser("getvar"); getvar.add_argument("name", nargs="?", default="all")
    flash = f.add_parser("flash"); flash.add_argument("partition"); flash.add_argument("image")
    erase = f.add_parser("erase"); erase.add_argument("partition")
    boot = f.add_parser("boot"); boot.add_argument("image")
    active = f.add_parser("set-active"); active.add_argument("slot", choices=["a", "b"])
    fastboot_reboot = f.add_parser("reboot"); fastboot_reboot.add_argument("mode", nargs="?", default="", choices=["", "bootloader", "recovery", "fastboot"])
    return p


def main() -> int:
    args = build_parser().parse_args()
    try:
        if args.tool == "adb":
            tool = ADB(args.serial, args.platform_tools)
            if args.cmd == "devices": print("\n".join(tool.devices()) or "No devices found.")
            elif args.cmd == "info":
                for key, value in tool.device_info().items(): print(f"{key:16}{value}")
            elif args.cmd == "start-server": print(tool.start_server())
            elif args.cmd == "kill-server": print(tool.kill_server())
            elif args.cmd == "shell": print(tool.shell(args.command))
            elif args.cmd == "install": print(tool.install(args.apk))
            elif args.cmd == "uninstall": print(tool.uninstall(args.package))
            elif args.cmd == "packages": print("\n".join(tool.list_packages(args.filter, args.third_party)))
            elif args.cmd == "push": print(tool.push(args.local, args.remote))
            elif args.cmd == "pull": print(tool.pull(args.remote, args.local))
            elif args.cmd == "screenshot": print(f"Saved to {tool.screenshot(args.out)}")
            elif args.cmd == "logcat": return tool.logcat(*args.args)
            elif args.cmd == "reboot": print(tool.reboot(args.mode) or "Rebooting...")
            elif args.cmd == "sideload": print(tool.sideload(args.zip))
            elif args.cmd == "connect": print(tool.connect(args.host))
            elif args.cmd == "tcpip": print(tool.tcpip())
        else:
            tool = Fastboot(args.serial, args.platform_tools)
            if args.cmd == "devices": print("\n".join(tool.devices()) or "No devices found.")
            elif args.cmd == "getvar": print(tool.getvar(args.name))
            elif args.cmd == "flash": _confirm(f"Flashing '{args.partition}' can brick your device if wrong."); print(tool.flash(args.partition, args.image))
            elif args.cmd == "erase": _confirm(f"Erasing '{args.partition}' is irreversible."); print(tool.erase(args.partition))
            elif args.cmd == "boot": print(tool.boot(args.image))
            elif args.cmd == "set-active": print(tool.set_active(args.slot))
            elif args.cmd == "reboot": print(tool.reboot(args.mode) or "Rebooting...")
            elif args.cmd == "unlock": _confirm("Unlocking the bootloader WIPES ALL DATA."); print(tool.oem_unlock())
            elif args.cmd == "lock": _confirm("Locking the bootloader WIPES ALL DATA."); print(tool.oem_lock())
            elif args.cmd == "wipe": _confirm("This WIPES ALL USER DATA."); print(tool.wipe_data())
    except KeyboardInterrupt:
        print("Interrupted.", file=sys.stderr)
        return 130
    except ToolError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
