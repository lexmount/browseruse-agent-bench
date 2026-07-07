"""
CLIAgent - Base class for agents that run external CLI tools via subprocess.

Agents that drive a CLI executable (e.g. `claude`, `agent-tars`) should inherit
from this class instead of BaseAgent directly. It provides:

  - _run_subprocess(): launch a process, drain stdout/stderr to files, handle timeout
  - _map_exit_status(): map returncode + execution_error → (env_status, agent_done)
"""

from __future__ import annotations

import logging
import os
import signal
import subprocess
import time
from collections.abc import Callable
from contextlib import suppress
from pathlib import Path
from threading import Thread
from typing import TextIO

from browseruse_bench.agents.base import BaseAgent
from browseruse_bench.utils import IS_WINDOWS

logger = logging.getLogger(__name__)


def _escalate_stop(
    process: subprocess.Popen,
    signal_group: Callable[[int], None],
    early_grace_seconds: float,
    kill_grace_seconds: float,
) -> int:
    """Wait for a process already sent SIGTERM, escalating to SIGKILL.

    Returns the exit code, or -1 when the process outlives even the
    post-SIGKILL grace (e.g. stuck in uninterruptible I/O) — never raises,
    so a captured result is not lost to a shutdown hiccup.
    """
    try:
        return process.wait(timeout=early_grace_seconds)
    except subprocess.TimeoutExpired:
        signal_group(getattr(signal, "SIGKILL", signal.SIGTERM))
    try:
        return process.wait(timeout=kill_grace_seconds)
    except subprocess.TimeoutExpired:
        logger.error(
            "Process %s survived SIGKILL for %ss; abandoning wait", process.pid, kill_grace_seconds
        )
        return -1


class CLIAgent(BaseAgent):
    """
    BaseAgent subclass for CLI-based agents (subprocess execution pattern).

    Subclasses must still implement run_task(). This class only adds shared
    subprocess utilities; it does not define any agent-specific logic.
    """

    def _run_subprocess(
        self,
        cmd: list[str],
        *,
        timeout: int,
        task_workspace: Path,
        cwd: Path | None = None,
        env: dict[str, str] | None = None,
        collect_stdout: bool = True,
        collect_stderr_as_stdout: bool = False,
        stdout_line_hook: Callable[[str], None] | None = None,
        stderr_line_hook: Callable[[str], None] | None = None,
        stop_predicate: Callable[[list[str]], bool] | None = None,
        terminate_process_group: bool = False,
        early_stop_grace_seconds: float = 5.0,
        kill_grace_seconds: float = 5.0,
    ) -> tuple[int, list[str], str | None]:
        """Launch *cmd*, draining stdout/stderr to files in *task_workspace*.

        Args:
            cmd: Full command + arguments list.
            timeout: Seconds before the process is killed.
            task_workspace: Directory where stdout.txt / stderr.txt are written.
            cwd: Working directory for the subprocess. Defaults to None (inherits
                 parent cwd). Pass task_workspace to prevent the child process from
                 loading project-level config/skills from the repo root.
            env: Optional environment dict passed to Popen. Defaults to None
                 (inherits parent environment).
            collect_stdout: Whether to accumulate stdout lines and return them.
                            Set False when output is written to disk by the
                            child process and parsed later (e.g. Agent-TARS).
            collect_stderr_as_stdout: Whether to append stderr lines to the
                            returned line buffer and evaluate stop_predicate on
                            them. Use only for CLIs that emit their machine
                            readable result on stderr.
            stdout_line_hook: Called with each raw stdout line for live logging.
            stderr_line_hook: Called with each raw stderr line for live logging.
            stop_predicate: Called with the collected stdout lines after each
                            line; returning True terminates the process early
                            with a zero exit status. For CLIs whose useful
                            output is complete before the process exits (e.g.
                            a child service keeps it alive). Requires
                            collect_stdout=True.
            terminate_process_group: Start the subprocess in its own process
                            group/session and terminate the whole group on
                            timeout or early stop. Use for CLIs that spawn
                            long-lived helper processes.
            early_stop_grace_seconds: Seconds to wait after stop_predicate
                            matches before force-killing.
            kill_grace_seconds: Seconds to wait after force-killing.

        Returns:
            ``(returncode, stdout_lines, execution_error)``
            - *returncode*: process exit code, or -1 on timeout/error; 0 when
              stopped early via *stop_predicate*.
            - *stdout_lines*: collected lines (empty when collect_stdout=False).
            - *execution_error*: human-readable error string, or None on success.

        Raises:
            FileNotFoundError: if the executable in *cmd[0]* is not found.
                               Callers should catch this and return an error AgentResult.
        """
        stdout_file = task_workspace / "stdout.txt"
        stderr_file = task_workspace / "stderr.txt"
        stdout_lines: list[str] = []
        execution_error: str | None = None
        returncode = -1
        stopped_early = False

        try:
            with (
                open(stdout_file, "w", encoding="utf-8") as f_out,
                open(stderr_file, "w", encoding="utf-8") as f_err,
            ):
                # FileNotFoundError propagates to caller if exe not found
                popen_kwargs: dict[str, object] = {
                    # DEVNULL, not inherited: CLIs that read piped stdin until
                    # EOF (codex exec prints "Reading additional input from
                    # stdin..." and blocks) hang for the full task timeout when
                    # the parent's stdin is open, e.g. under a batch script.
                    "stdin": subprocess.DEVNULL,
                    "stdout": subprocess.PIPE,
                    "stderr": subprocess.PIPE,
                    "text": True,
                    "encoding": "utf-8",
                    "errors": "replace",
                    "bufsize": 1,
                    "cwd": str(cwd) if cwd is not None else None,
                    "env": env,
                }
                if terminate_process_group:
                    if IS_WINDOWS:
                        # Known limitation: without a CTRL_BREAK_EVENT sender
                        # only the direct child is terminated on Windows;
                        # helper processes are reaped by the runner watchdog.
                        popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
                    else:
                        popen_kwargs["start_new_session"] = True

                process = subprocess.Popen(cmd, **popen_kwargs)

                def _signal_process_group(sig: int) -> None:
                    if terminate_process_group and not IS_WINDOWS:
                        try:
                            os.killpg(process.pid, sig)
                            return
                        except ProcessLookupError:
                            return
                        except OSError as exc:
                            logger.error(
                                "killpg(%s, %s) failed: %s; falling back to the direct child",
                                process.pid, sig, exc,
                            )
                    with suppress(ProcessLookupError, OSError):
                        kill_signal = getattr(signal, "SIGKILL", None)
                        if kill_signal is not None and sig == kill_signal:
                            process.kill()
                        else:
                            process.terminate()

                def _drain_stdout(stream: TextIO, fh: TextIO) -> None:
                    nonlocal stopped_early
                    for line in iter(stream.readline, ""):
                        if not line:
                            continue
                        fh.write(line)
                        fh.flush()
                        if collect_stdout:
                            stdout_lines.append(line)
                        if stdout_line_hook:
                            stdout_line_hook(line)
                        if stop_predicate and not stopped_early and stop_predicate(stdout_lines):
                            stopped_early = True
                            _signal_process_group(signal.SIGTERM)
                            return

                def _drain_stderr(stream: TextIO, fh: TextIO) -> None:
                    nonlocal stopped_early
                    for line in iter(stream.readline, ""):
                        if not line:
                            continue
                        fh.write(line)
                        fh.flush()
                        if collect_stdout and collect_stderr_as_stdout:
                            stdout_lines.append(line)
                        if stderr_line_hook:
                            stderr_line_hook(line)
                        if stop_predicate and not stopped_early and stop_predicate(stdout_lines):
                            stopped_early = True
                            _signal_process_group(signal.SIGTERM)
                            return

                t_out = Thread(target=_drain_stdout, args=(process.stdout, f_out))
                t_err = Thread(target=_drain_stderr, args=(process.stderr, f_err))
                t_out.start()
                t_err.start()

                deadline = time.monotonic() + timeout
                while True:
                    if stopped_early:
                        returncode = _escalate_stop(
                            process, _signal_process_group,
                            early_stop_grace_seconds, kill_grace_seconds,
                        )
                        break
                    if stop_predicate and not stopped_early and stop_predicate(stdout_lines):
                        stopped_early = True
                        _signal_process_group(signal.SIGTERM)
                        continue
                    try:
                        returncode = process.wait(timeout=0.25)
                        break
                    except subprocess.TimeoutExpired:
                        if time.monotonic() < deadline:
                            continue
                        _signal_process_group(signal.SIGTERM)
                        _escalate_stop(
                            process, _signal_process_group,
                            early_stop_grace_seconds, kill_grace_seconds,
                        )
                        execution_error = f"Timeout after {timeout} seconds"
                        returncode = -1
                        break

                t_out.join(timeout=kill_grace_seconds)
                t_err.join(timeout=kill_grace_seconds)

                # A drain thread can match the predicate on the dying
                # process's final output flush AFTER the timeout branch has
                # already decided; never rewrite a timeout into a success.
                if stopped_early and execution_error is None:
                    returncode = 0

        except FileNotFoundError:
            raise
        except (OSError, subprocess.SubprocessError) as exc:
            execution_error = str(exc)

        return returncode, stdout_lines, execution_error

    @staticmethod
    def _map_exit_status(
        returncode: int | None,
        execution_error: str | None,
        has_result: bool = True,
    ) -> tuple[str, str]:
        """Map subprocess exit conditions to ``(env_status, agent_done)``.

        Args:
            returncode: Process exit code (or -1 / None on abnormal exit).
            execution_error: Non-None means the process failed or timed out.
            has_result: Whether usable output was produced. When False and
                        returncode is non-zero, treat as error even if
                        execution_error is None.

        Returns:
            A ``(env_status, agent_done)`` tuple compatible with AgentResult.
        """
        if execution_error and "Timeout" in execution_error:
            return "success", "timeout"
        if execution_error or (returncode not in (0, None) and not has_result):
            return "failed", "error"
        return "success", "done"
