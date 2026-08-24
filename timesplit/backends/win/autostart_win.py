"""Starting TimeSplit when you log in, via Task Scheduler.

Task Scheduler over the alternatives because it is the only one that can
express the settings that actually matter here:

  <Delay>PT30S</Delay>            let the desktop settle before starting
  StopIfGoingOnBatteries=false    the single most common cause of "it just
                                  stopped working" on a laptop
  RestartOnFailure                survive a crash
  IgnoreNew                       never two copies double-counting the day

The Startup folder can express none of those and flashes a console window; the
HKCU\\...\\Run key cannot either, and looks enough like malware persistence that
some antivirus products flag it.

One sharp edge, handled explicitly below: schtasks /XML rejects UTF-8. The file
must be UTF-16LE with a byte-order mark.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from ... import paths
from ...logging_setup import get as get_logger

log = get_logger("backends.autostart")

TASK_NAME = "TimeSplit"

TASK_XML = """<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.4" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Description>Tracks how your computer time splits between your two jobs.</Description>
    <URI>\\{task_name}</URI>
  </RegistrationInfo>
  <Triggers>
    <LogonTrigger>
      <Enabled>true</Enabled>
      <UserId>{user}</UserId>
      <Delay>PT30S</Delay>
    </LogonTrigger>
  </Triggers>
  <Principals>
    <Principal id="Author">
      <UserId>{user}</UserId>
      <LogonType>InteractiveToken</LogonType>
      <RunLevel>LeastPrivilege</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <AllowHardTerminate>true</AllowHardTerminate>
    <StartWhenAvailable>true</StartWhenAvailable>
    <RunOnlyIfNetworkAvailable>false</RunOnlyIfNetworkAvailable>
    <IdleSettings>
      <StopOnIdleEnd>false</StopOnIdleEnd>
      <RestartOnIdle>false</RestartOnIdle>
    </IdleSettings>
    <AllowStartOnDemand>true</AllowStartOnDemand>
    <Enabled>true</Enabled>
    <Hidden>false</Hidden>
    <RunOnlyIfIdle>false</RunOnlyIfIdle>
    <WakeToRun>false</WakeToRun>
    <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>
    <Priority>7</Priority>
    <RestartOnFailure>
      <Interval>PT5M</Interval>
      <Count>3</Count>
    </RestartOnFailure>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>{command}</Command>
      <Arguments>{arguments}</Arguments>
      <WorkingDirectory>{working_dir}</WorkingDirectory>
    </Exec>
  </Actions>
</Task>
"""


def _pythonw() -> str:
    """pythonw.exe runs without a console window."""
    executable = Path(sys.executable)
    candidate = executable.with_name("pythonw.exe")
    if candidate.exists():
        return str(candidate)
    return str(executable)


def _current_user() -> str:
    import os

    domain = os.environ.get("USERDOMAIN", "")
    user = os.environ.get("USERNAME", "")
    return f"{domain}\\{user}" if domain and user else user


def _escape(value: str) -> str:
    return (
        value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    )


class TaskSchedulerAutostart:
    def __init__(self) -> None:
        self.task_name = TASK_NAME
        self.xml_path = paths.data_dir() / "autostart-task.xml"

    def _write_xml(self) -> Path:
        import timesplit

        package_root = Path(timesplit.__file__).resolve().parent.parent
        xml = TASK_XML.format(
            task_name=self.task_name,
            user=_escape(_current_user()),
            command=_escape(_pythonw()),
            arguments=_escape("-m timesplit run"),
            working_dir=_escape(str(package_root)),
        )
        self.xml_path.parent.mkdir(parents=True, exist_ok=True)
        # schtasks /XML rejects UTF-8. This must be UTF-16LE with a BOM.
        self.xml_path.write_bytes(b"\xff\xfe" + xml.encode("utf-16-le"))
        return self.xml_path

    def install(self) -> tuple[bool, str]:
        try:
            xml_path = self._write_xml()
        except OSError as exc:
            return False, f"Could not write the task file: {exc}"

        result = _run([
            "schtasks", "/Create", "/TN", self.task_name, "/XML", str(xml_path), "/F"
        ])
        if result.returncode == 0:
            return True, (
                f"TimeSplit will start automatically when you log in "
                f"(scheduled task “{self.task_name}”, 30 seconds after logon)."
            )
        message = (result.stderr or result.stdout or "").strip()
        log.error("schtasks /Create failed: %s", message)
        return False, (
            f"Could not register the logon task: {message}\n"
            f"As a fallback, put a shortcut to "
            f'"{_pythonw()}" -m timesplit run in your Startup folder '
            f"(press Win+R and enter shell:startup)."
        )

    def uninstall(self) -> tuple[bool, str]:
        result = _run(["schtasks", "/Delete", "/TN", self.task_name, "/F"])
        if result.returncode == 0:
            return True, "TimeSplit will no longer start automatically."
        message = (result.stderr or result.stdout or "").strip()
        if "cannot find" in message.lower():
            return True, "There was no logon task to remove."
        return False, f"Could not remove the logon task: {message}"

    def status(self) -> tuple[bool, str]:
        result = _run(["schtasks", "/Query", "/TN", self.task_name])
        if result.returncode == 0:
            return True, "registered with Task Scheduler"
        return False, "not registered"


def _run(command: list[str]) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(
            command, capture_output=True, text=True, timeout=30,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return subprocess.CompletedProcess(command, 1, "", str(exc))
