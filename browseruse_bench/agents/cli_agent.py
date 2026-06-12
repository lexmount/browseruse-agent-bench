"""
CLIAgent - Base class for agents that run external CLI tools via subprocess.

Agents that drive a CLI executable (e.g. `claude`, `agent-tars`) should inherit
from this class instead of BaseAgent directly. It provides:

  - _run_subprocess(): launch a process, drain stdout/stderr to files, handle timeout
  - _map_exit_status(): map returncode + execution_error → (env_status, agent_done)
"""

from __future__ import annotations

import subprocess
from collections.abc import Callable
from pathlib import Path
from threading import Thread
from typing import TextIO

from browseruse_bench.agents.base import BaseAgent


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
        stdout_line_hook: Callable[[str], None] | None = None,
        stderr_line_hook: Callable[[str], None] | None = None,
        stop_predicate: Callable[[list[str]], bool] | None = None,
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
            stdout_line_hook: Called with each raw stdout line for live logging.
            stderr_line_hook: Called with each raw stderr line for live logging.
            stop_predicate: Called with the collected stdout lines after each
                            line; returning True terminates the process early
                            with a zero exit status. For CLIs whose useful
                            output is complete before the process exits (e.g.
                            a child service keeps it alive). Requires
                            collect_stdout=True.

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
                process = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    bufsize=1,
                    cwd=str(cwd) if cwd is not None else None,
                    env=env,
                )

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
                            process.terminate()
                            return

                def _drain_stderr(stream: TextIO, fh: TextIO) -> None:
                    for line in iter(stream.readline, ""):
                        if not line:
                            continue
                        fh.write(line)
                        fh.flush()
                        if stderr_line_hook:
                            stderr_line_hook(line)

                t_out = Thread(target=_drain_stdout, args=(process.stdout, f_out))
                t_err = Thread(target=_drain_stderr, args=(process.stderr, f_err))
                t_out.start()
                t_err.start()

                try:
                    returncode = process.wait(timeout=timeout)
                except subprocess.TimeoutExpired:
                    process.terminate()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                    execution_error = f"Timeout after {timeout} seconds"
                    returncode = -1

                t_out.join(timeout=5)
                t_err.join(timeout=5)

                if stopped_early:
                    returncode = 0
                    execution_error = None

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
