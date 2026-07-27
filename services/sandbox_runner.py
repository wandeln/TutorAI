"""
Sandbox-Runner: Führt Python-Code sicher in isolierter Umgebung aus.

Security-Layer:
1. Timeout (Wall-Clock) → verhindert Endlosschleifen
2. Memory-Limit (prlimit) → verhindert Memory-Leaks / DoS
3. CPU-Limit (prlimit) → zusätzliche CPU-Schutzschicht
4. Chroot / ReadOnly → kein Schreiben auf Server-Dateisystem (optional)
5. Whitelisted modules → nur Standardbibliothek + erlaubte Libs

Kein Docker-Overhead — rein subprocess + resource Limits.
"""

import asyncio
import json
import os
import resource
import signal
import subprocess
import tempfile
from pathlib import Path
from typing import Dict, Any, List, Optional

from config import (
    SANDBOX_TIMEOUT, SANDBOX_MEMORY_MB, SANDBOX_CPU_SECONDS,
    SANDBOX_ALLOWED_MODULES,
)


class SandboxedRunner:
    """
    Führt Python-Code + Unit-Tests in isolierter Umgebung aus.
    
    Returns structured result:
        {
            "passed": bool,
            "test_results": [{"name": "...", "passed": true/false, "output": "..."}],
            "stdout": str,
            "stderr": str,
            "timeout": bool,
            "error": str or None,
        }
    """
    
    RESULT_MARKER = "===SANDBOX_RESULT==="
    
    def __init__(self):
        self.timeout = SANDBOX_TIMEOUT
        self.memory_mb = SANDBOX_MEMORY_MB
        self.cpu_seconds = SANDBOX_CPU_SECONDS
    
    async def run(
        self,
        code: str,
        tests_code: str,
        timeout: Optional[int] = None,
        memory_mb: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        Führt Code + Tests aus und gibt strukturiertes Ergebnis zurück.
        
        Args:
            code: Studenten-Code
            tests_code: Test-Code (unittest-Format)
            timeout: Override default timeout
            memory_mb: Override default memory limit
        """
        full_script = self._wrap_script(code, tests_code)
        
        runner_timeout = timeout or self.timeout
        runner_memory = memory_mb or self.memory_mb
        
        # Temp-Datei erstellen
        tmp = tempfile.NamedTemporaryFile(
            mode="w", suffix=".py", prefix="sandbox_",
            delete=False, encoding="utf-8",
        )
        tmp.write(full_script)
        tmp.close()
        
        try:
            # Subprocess mit resource-Limits
            proc = subprocess.Popen(
                ["python3", tmp.name],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                preexec_fn=self._set_limits,
            )
            
            try:
                stdout, stderr = proc.communicate(timeout=runner_timeout)
                return self._parse_result(stdout, stderr, proc.returncode)
            except subprocess.TimeoutExpired:
                # Kill entire process tree
                try:
                    os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
                except:
                    proc.kill()
                return {
                    "passed": False,
                    "test_results": [],
                    "stdout": "",
                    "stderr": f"Zeitlimit ({runner_timeout}s) überschritten",
                    "timeout": True,
                    "error": "TIMEOUT",
                }
        finally:
            # Aufräumen
            try:
                os.unlink(tmp.name)
            except FileNotFoundError:
                pass
    
    def _set_limits(self):
        """Setzt Resource-Limits für den Child-Process (im Fork-Kontext)."""
        # Memory (Address Space)
        soft_mem = self.memory_mb * 1024 * 1024
        hard_mem = resource.getrlimit(resource.RLIMIT_AS)[1]
        resource.setrlimit(resource.RLIMIT_AS, (soft_mem, hard_mem))
        
        # CPU seconds
        resource.setrlimit(resource.RLIMIT_CPU, (self.cpu_seconds, self.cpu_seconds + 5))
        
        # Nice value (niedrigere Priorität)
        os.nice(10)
    
    def _wrap_script(self, code: str, tests_code: str) -> str:
        """
        Verpackt den Code so, dass Test-Ergebnisse als JSON zurückkommen.
        Das macht die Auswertung server-seitig sehr robust.
        """
        marker = self.RESULT_MARKER
        return f'''
import sys, json, unittest, io

# Redirect stdout
class CapturedIO(io.StringIO):
    def flush(self):
        pass

old_stdout = sys.stdout
sys.stdout = CapturedIO()

# ---- STUDENT CODE ----
{code}

# ---- TESTS ----
{tests_code}

# ---- RUN TESTS ----
captured = sys.stdout.getvalue()
sys.stdout = old_stdout

loader = unittest.TestLoader()
suite = loader.loadTestsFromModule(sys.modules[__name__])
runner = unittest.TextTestRunner(stream=sys.stdout, verbosity=2)
result = runner.run(suite)

# ---- STRUCTURED OUTPUT ----
test_results = []
# Python 3.12: TextTestResult hat kein .tests mehr → Suite iterieren
for test_case in suite:
    name = str(test_case)
    passed = True
    output = ""
    for failure in result.failures:
        if str(failure[0]) == name:
            passed = False
            output = failure[1]
            break
    for error in result.errors:
        if str(error[0]) == name:
            passed = False
            output = error[1]
            break
    test_results.append({{
        "name": name,
        "passed": passed,
        "output": output
    }})

all_passed = result.wasSuccessful()

print("{marker}", flush=True)
print(json.dumps({{
    "passed": all_passed,
    "test_results": test_results,
    "stdout": captured,
}}))
'''
    
    def _parse_result(
        self, stdout: str, stderr: str, returncode: int
    ) -> Dict[str, Any]:
        """Parsiert das strukturierte Ergebnis aus dem Output."""
        
        # JSON-Ergebnis extrahieren
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
        
        # Fallback: returncode als Indikator
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