"""
Sandbox-Runner: Fuehrt Python-Code sicher in isolierter Umgebung aus.

Security-Layer:
1. Timeout (Wall-Clock) via asyncio.wait_for -> verhindert Endlosschleifen
2. CPU-Limit (RLIMIT_CPU) -> zusätzliche CPU-Schutzschicht
3. signal.alarm(10) im Skript ->.killt stuck Child-Processes
4. os.killpg() -> Prozessgruppe wird komplett entfernt

Kein RLIMIT_AS (Memory-Limit) — es bricht matplotlib/numpy OpenBLAS shared libs.
Kein Docker-Overhead — rein asyncio subprocess + resource Limits.
"""

import asyncio
import json
import os
import re
import resource
import signal
import shutil
import tempfile
from typing import Dict, Any, Optional, Tuple

from config import (
    SANDBOX_TIMEOUT, SANDBOX_MEMORY_MB, SANDBOX_CPU_SECONDS,
)


class SandboxedRunner:
    """
    Fuehrt Python-Code + Unit-Tests in isolierter Umgebung aus.
    """

    RESULT_MARKER = "===SANDBOX_RESULT==="

    def __init__(self):
        self.timeout = SANDBOX_TIMEOUT
        self.memory_mb = SANDBOX_MEMORY_MB
        self.cpu_seconds = SANDBOX_CPU_SECONDS

    # ──────────────────────────────────────────────────────────────
    # PUBLIC: Code + Tests
    # ──────────────────────────────────────────────────────────────

    async def run(
        self,
        code: str,
        tests_code: str,
        timeout: Optional[int] = None,
        memory_mb: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Fuehrt Code + Tests aus und gibt strukturiertes Ergebnis zurueck."""
        full_script = self._wrap_script(code, tests_code)
        runner_timeout = timeout or self.timeout
        runner_memory = memory_mb or self.memory_mb

        tmp = tempfile.NamedTemporaryFile(
            mode="w", suffix=".py", prefix="sandbox_",
            delete=False, encoding="utf-8",
        )
        tmp.write(full_script)
        tmp.close()

        try:
            result = await self._execute_async(tmp.name, runner_timeout, runner_memory)
            return self._parse_result(result["stdout"], result["stderr"], result.get("returncode", 1))
        finally:
            try:
                os.unlink(tmp.name)
            except FileNotFoundError:
                pass

    # ──────────────────────────────────────────────────────────────
    # PUBLIC: Code ohne Tests (mit Matplotlib-Support)
    # ──────────────────────────────────────────────────────────────

    async def run_code_only(self, code: str, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        Fuehrt Code aus OHNE Tests — gibt stdout/stderr + Matplotlib-Bilder zurueck.

        stderr-Zeilennummern werden so korrigiert, dass sie zum Studenten-Editor passen.
        """
        img_dir = tempfile.mkdtemp(prefix="sandbox_img_")
        full_script, line_offset = self._wrap_code_only(code, img_dir)
        runner_timeout = timeout or self.timeout
        runner_memory = self.memory_mb

        tmp = tempfile.NamedTemporaryFile(
            mode="w", suffix=".py", prefix="sandbox_",
            delete=False, encoding="utf-8",
        )
        tmp.write(full_script)
        tmp.close()

        try:
            result = await self._execute_async(tmp.name, runner_timeout, runner_memory)
            images = self._extract_images(result.get("stdout", ""))
            clean_stdout = self._strip_image_markers(result.get("stdout", ""))
            result["stdout"] = clean_stdout
            result["images"] = images
            # Korrigiere Zeilennummern im stderr (funktioniert auch bei SyntaxError)
            result["stderr"] = self._adjust_stderr_line_numbers(result["stderr"], line_offset)
            return result
        finally:
            try:
                os.unlink(tmp.name)
            except FileNotFoundError:
                pass
            try:
                shutil.rmtree(img_dir, ignore_errors=True)
            except Exception:
                pass

    # ──────────────────────────────────────────────────────────────
    # ASYNC EXECUTION (non-blocking)
    # ──────────────────────────────────────────────────────────────

    async def _execute_async(
        self, script_path: str, timeout: int, memory_mb: int
    ) -> Dict[str, Any]:
        """
        Fuehrt ein Skript asynchron in isolierter Umgebung aus.

        Ansatz: proc.wait() mit asyncio.wait_for(timeout) -> blockiert NICHT
        fueher als timeout Sekunden. Nach dem Exit/Kill lesen wir stdout/stderr.
        """
        proc = await asyncio.create_subprocess_exec(
            "python3", "-u", script_path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=True,
        )

        try:
            timed_out = False
            try:
                await asyncio.wait_for(proc.wait(), timeout=timeout)
            except asyncio.TimeoutError:
                timed_out = True

            if proc.returncode is None:
                timed_out = True
                try:
                    os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
                except (OSError, ProcessLookupError):
                    try:
                        proc.kill()
                    except (OSError, ProcessLookupError):
                        pass
                try:
                    await asyncio.wait_for(proc.wait(), timeout=2)
                except asyncio.TimeoutError:
                    pass

            stdout = ""
            stderr = ""
            if proc.stdout is not None:
                try:
                    stdout_bytes = await asyncio.wait_for(proc.stdout.read(), timeout=2)
                    stdout = stdout_bytes.decode("utf-8", errors="replace")
                except Exception:
                    stdout = ""
            if proc.stderr is not None:
                try:
                    stderr_bytes = await asyncio.wait_for(proc.stderr.read(), timeout=2)
                    stderr = stderr_bytes.decode("utf-8", errors="replace")
                except Exception:
                    stderr = ""

            if timed_out:
                stderr = f"Zeitlimit ({timeout}s) ueberschritten"

            return {
                "stdout": stdout,
                "stderr": stderr.strip(),
                "returncode": proc.returncode if proc.returncode is not None else -1,
            }

        except Exception as e:
            try:
                proc.kill()
            except (OSError, ProcessLookupError):
                pass
            return {
                "stdout": "",
                "stderr": f"Sandbox-Fehler: {str(e)}",
                "returncode": -1,
            }

    # ──────────────────────────────────────────────────────────────
    # SCRIPT WRAPPERS
    # ──────────────────────────────────────────────────────────────

    def _wrap_script(self, code: str, tests_code: str) -> str:
        """Verpackt Code + Tests mit Resource-Limits im Skript selbst."""
        tests_code = self._clean_test_code(tests_code)
        marker = self.RESULT_MARKER
        cpu_soft = self.cpu_seconds
        cpu_hard = self.cpu_seconds + 5

        # Wrapper nutzt _ut statt unittest, damit matplotlib unittest nicht kaputt macht
        return "\n".join([
            "import sys, json, unittest as _ut, io, resource, signal, traceback",
            "",
            "# --- Resource Limits (nur CPU) ---",
            f"resource.setrlimit(resource.RLIMIT_CPU, ({cpu_soft}, {cpu_hard}))",
            "",
            "signal.alarm(10)",
            "",
            "# --- Matplotlib-Schutz: plt.show() => No-Op (kein Display in Sandbox) ---",
            "try:",
            "    import matplotlib",
            "    matplotlib.use('Agg')",
            "    import matplotlib.pyplot as _mpl_plt",
            "    _mpl_plt.show = lambda: None",
            "except ImportError:",
            "    pass",
            "",
            "class _CapturedIO(io.StringIO):",
            "    def flush(self):",
            "        pass",
            "",
            "_old_stdout = sys.stdout",
            "sys.stdout = _CapturedIO()",
            "",
            "# ---- STUDENT CODE ----",
            # Capture student code errors so tests can still run
            "_code_error = None",
            "try:",
            *["    " + line for line in code.split("\n")],
            "except Exception as _e:",
            "    _code_error = traceback.format_exc()",
            "",
            "# ---- TESTS ----",
            "import unittest",
            tests_code,
            "",
            "# ---- RUN TESTS ----",
            "_captured = sys.stdout.getvalue()",
            "sys.stdout = _old_stdout",
            "",
            "_loader = _ut.TestLoader()",
            "_suite = _loader.loadTestsFromModule(sys.modules[__name__])",
            "",
            "# Nur echte TestCase-Instanzen sammeln (filtert matplotlib etc.)",
            "_test_cases = []",
            "def _collect_tests(s):",
            "    if hasattr(s, '_tests'):",
            "        for _item in s._tests:",
            "            if hasattr(_item, '_tests'):",
            "                _collect_tests(_item)",
            "            elif hasattr(_item, 'shortDescription'):",
            "                _test_cases.append(_item)",
            "_collect_tests(_suite)",
            "",
            "_runner = _ut.TextTestRunner(stream=sys.stdout, verbosity=2)",
            "_result = _runner.run(_suite)",
            "",
            "_test_results = []",
            "for _tc in _test_cases:",
            "    _name = str(_tc)",
            "    _passed = True",
            '    _output = ""',
            "    for _f in _result.failures:",
            "        if str(_f[0]) == _name:",
            "            _passed = False",
            "            _output = _f[1]",
            "            break",
            "    for _e in _result.errors:",
            "        if str(_e[0]) == _name:",
            "            _passed = False",
            "            _output = _e[1]",
            "            break",
            "    _test_results.append({",
            '        "name": _name,',
            '        "passed": _passed,',
            '        "output": _output',
            "    })",
            "",
            "_all_passed = _result.wasSuccessful()",
            "",
            "# Wenn es Code-Fehler gab, als ersten Eintrag einfuegen",
            "if _code_error:",
            '    _test_results.insert(0, {"name": "Code-Ausfuehrung", "passed": False, "output": _code_error})',
            '    _all_passed = False',
            "",
            f'print("{marker}", flush=True)',
            "print(json.dumps({",
            '    "passed": _all_passed,',
            '    "test_results": _test_results,',
            '    "stdout": _captured,',
            "}))",
        ])

    def _wrap_code_only(self, code: str, img_dir: str) -> Tuple[str, int]:
        """
        Wrapper fuer Code ohne Tests mit Resource-Limits + Matplotlib-Support.

        plt.show() wird automatisch in PNG umgewandelt und als Base64
        im stdout ausgegeben (markiert mit IMG_MARKER).

        Gibt (script, line_offset) zurueck. Der line_offset gibt an,
        wie viele Zeilen VOR dem Studenten-Code liegen.

        Die Zeilenkorrektur erfolgt im Backend (run_code_only),
        damit sie auch bei Syntax/IndentationError funktioniert.
        """
        cpu_soft = self.cpu_seconds
        cpu_hard = self.cpu_seconds + 5

        # Preamble: alle Zeilen die VOR dem Studenten-Code kommen.
        # Als Liste aufgebaut -> exakte Zeilenzaehlung.
        preamble = [
            "import resource, signal, os, traceback, sys",
            "",
            f"resource.setrlimit(resource.RLIMIT_CPU, ({cpu_soft}, {cpu_hard}))",
            "",
            "signal.alarm(10)",
            "",
            "import base64, os",
            f"_plot_dir = '{img_dir}'",
            "_plot_count = 0",
            "def _capture_plots():",
            "    global _plot_count",
            "    try:",
            "        import matplotlib.pyplot as plt",
            "    except ImportError:",
            "        return",
            "    figs = []",
            "    for name, obj in list(globals().items()):",
            "        if isinstance(obj, plt.Figure) and not getattr(obj, '_sandbox_emitted', False):",
            "            figs.append(obj)",
            "            obj._sandbox_emitted = True",
            "    try:",
            "        current = plt.gcf()",
            "        if current not in figs and not getattr(current, '_sandbox_emitted', False) and len(current.get_axes()) > 0:",
            "            figs.append(current)",
            "            current._sandbox_emitted = True",
            "    except Exception:",
            "        pass",
            "    for fig in figs:",
            "        _plot_count += 1",
            "        path = os.path.join(_plot_dir, 'plot_' + str(_plot_count) + '.png')",
            "        fig.savefig(path, dpi=100, bbox_inches='tight')",
            "        plt.close(fig)",
            "        with open(path, 'rb') as f:",
            "            b64 = base64.b64encode(f.read()).decode()",
            "        print('===PLOT_BASE64_START===' + b64 + '===PLOT_BASE64_END===', flush=True)",
            "",
            "# Monkey-patch plt.show()",
            "try:",
            "    import matplotlib.pyplot as plt",
            "    plt.show = _capture_plots",
            "except ImportError:",
            "    pass",
            "",
            "# ---- STUDENT CODE ----",
        ]

        # Der Studenten-Code beginnt bei Zeile len(preamble) + 1 (1-based)
        line_offset = len(preamble)

        # Fuege Studenten-Code direkt ein (kein extra try/except wrapper)
        for line in code.split("\n"):
            preamble.append(line)

        # Rest nach dem Code
        preamble.extend([
            "",
            "# Plots am Ende noch einmal erfassen (falls kein plt.show())",
            "_capture_plots()",
        ])

        return "\n".join(preamble), line_offset

    def _adjust_stderr_line_numbers(self, stderr: str, offset: int) -> str:
        """
        Subtrahiert den Preamble-Offset von allen 'line N' Referenzen im stderr.

        Funktioniert fuer Tracebacks von:
        - RuntimeError / ZeroDivisionError etc. (Traceback + line N)
        - SyntaxError / IndentationError (File ... line N)
        """
        if not stderr or offset <= 0:
            return stderr

        def _replace_line(m: re.Match) -> str:
            prefix = m.group(1)       # z.B. "  File \"/tmp/...\" line "
            num = int(m.group(2))     # Original-Zeilennummer
            corrected = max(num - offset, 1)
            return prefix + str(corrected)

        # Match: (optional whitespace + File ... line ) + digits
        pattern = r'(\s*File\s+[^)]+line\s+)(\d+)'
        return re.sub(pattern, _replace_line, stderr, flags=re.MULTILINE)

    # ──────────────────────────────────────────────────────────────
    # UTILITIES
    # ──────────────────────────────────────────────────────────────

    def _extract_images(self, stdout: str) -> list:
        """Extrahiert Base64-Bilder aus stdout mit Markern."""
        images = []
        start_marker = "===PLOT_BASE64_START==="
        end_marker = "===PLOT_BASE64_END==="
        start = 0
        while True:
            begin = stdout.find(start_marker, start)
            if begin == -1:
                break
            begin += len(start_marker)
            end = stdout.find(end_marker, begin)
            if end == -1:
                break
            b64_data = stdout[begin:end].strip()
            if b64_data:
                images.append({"format": "png", "base64": b64_data})
            start = end + len(end_marker)
        return images

    def _strip_image_markers(self, stdout: str) -> str:
        """Entfernt Bild-Marker + Base64-Daten aus stdout."""
        result = stdout
        start_marker = "===PLOT_BASE64_START==="
        end_marker = "===PLOT_BASE64_END==="
        while True:
            begin = result.find(start_marker)
            if begin == -1:
                break
            end = result.find(end_marker, begin) + len(end_marker)
            if end < len(end_marker):
                break
            result = result[:begin] + result[end:]
        return result

    @staticmethod
    def _clean_test_code(tests_code: str) -> str:
        """
        Entfernt 'if __name__ == "__main__": unittest.main()' aus Test-Code,
        da unser Runner die Tests selbst ausfuehrt.
        """
        cleaned = ""
        skip = False
        for line in tests_code.split("\n"):
            stripped = line.lstrip()
            if not skip and stripped.startswith("if __name__"):
                skip = True
                continue
            if skip:
                if stripped and not line[0].isspace():
                    skip = False
                    cleaned += line + "\n"
            else:
                cleaned += line + "\n"
        return cleaned

    def _parse_result(
        self, stdout: str, stderr: str, returncode: int
    ) -> Dict[str, Any]:
        """Parsiert das strukturierte Ergebnis aus dem Output."""

        test_results = []
        passed = False

        if self.RESULT_MARKER in stdout:
            json_str = stdout.split(self.RESULT_MARKER, 1)[1].strip()
            try:
                data = json.loads(json_str)
                passed = data.get("passed", False)
                test_results = data.get("test_results", [])
            except json.JSONDecodeError:
                pass

        if not test_results:
            passed = returncode == 0

        return {
            "passed": passed,
            "test_results": test_results,
            "stdout": stdout.split(self.RESULT_MARKER)[0] if self.RESULT_MARKER in stdout else stdout,
            "stderr": stderr.strip(),
            "timeout": False,
            "error": None if passed else stderr.strip() or f"Exit code: {returncode}",
        }


sandbox_runner = SandboxedRunner()
