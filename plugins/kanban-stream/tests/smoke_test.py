#!/usr/bin/env python3
"""kanban-stream smoke tests — stdlib only, no Hermes install required.

Run:  python3 tests/smoke_test.py
Exit: 0 = all checks passed, 1 = at least one failed.

These are distribution sanity checks, not a full test suite: they prove
the package is internally consistent (manifest vs code), compiles, has
no hardcoded machine paths, and that the pure helper functions behave.
The live behaviour (Telegram round-trip, hook wiring) is covered by
`hermes plugins doctor kanban-stream --ci` on a real install.
"""

import importlib.util
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INIT = os.path.join(ROOT, "__init__.py")
STEER = os.path.join(ROOT, "bin", "steer.py")
YAML = os.path.join(ROOT, "plugin.yaml")

failures = []


def check(name, ok, detail=""):
    print("%s %s%s" % ("PASS" if ok else "FAIL", name,
                       (" — " + detail) if detail and not ok else ""))
    if not ok:
        failures.append(name)


# 1. Both Python files compile.
for path in (INIT, STEER):
    try:
        compile(open(path).read(), path, "exec")
        check("compile %s" % os.path.relpath(path, ROOT), True)
    except SyntaxError as e:
        check("compile %s" % os.path.relpath(path, ROOT), False, str(e))

init_src = open(INIT).read()
yaml_src = open(YAML).read()

# 2. plugin.yaml hooks == hooks registered in code.
yaml_hooks = sorted(re.findall(r"^\s+-\s+(\w+)\s*$", yaml_src, re.M))
yaml_hooks = [h for h in yaml_hooks
              if h in ("pre_tool_call", "post_tool_call", "pre_llm_call",
                       "post_api_request", "kanban_task_completed",
                       "kanban_task_blocked")]
code_hooks = sorted(re.findall(r'register_hook\("(\w+)"', init_src))
check("manifest hooks match register() calls", yaml_hooks == code_hooks,
      "yaml=%s code=%s" % (yaml_hooks, code_hooks))

# 3. Required env vars are declared in the manifest.
for var in ("KANBAN_STREAM_BOT_TOKEN", "KANBAN_STREAM_ALLOWED_USERS"):
    check("requires_env declares %s" % var, var in yaml_src)

# 4. No hardcoded machine-specific absolute paths in the code.
for path in (INIT, STEER):
    src = open(path).read()
    bad = [m for m in re.findall(r"/home/\w+", src)]
    check("no hardcoded /home paths in %s" % os.path.relpath(path, ROOT),
          not bad, str(bad[:3]))

# 5. Version consistency: plugin.yaml vs module docstring.
yaml_ver = re.search(r'version:\s*"([\d.]+)"', yaml_src)
doc_ver = re.search(r"v(\d+\.\d+\.\d+)", init_src[:400])
check("version consistent (yaml vs docstring)",
      bool(yaml_ver and doc_ver and yaml_ver.group(1) == doc_ver.group(1)),
      "yaml=%s doc=%s" % (yaml_ver and yaml_ver.group(1),
                          doc_ver and doc_ver.group(1)))

# 6. Pure helpers behave (import the plugin module standalone — its
#    top-level imports are stdlib-only by design).
os.environ.setdefault("HERMES_KANBAN_TASK", "t_smoketest")
spec = importlib.util.spec_from_file_location("kanban_stream", INIT)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

check("_is_error_result: error dict",
      mod._is_error_result({"error": "boom"}) is True)
check("_is_error_result: success dict mentioning errors is not an error",
      mod._is_error_result({"output": "0 errors found in log"}) is False)
check("_is_error_result: None is not an error",
      mod._is_error_result(None) is False)
check("_arg_summary: command truncated to 80 chars",
      len(mod._arg_summary({"command": "x" * 200})) <= 91)
stream = mod._TaskStream("t_smoketest")
text = stream._format()
check("_format: renders task id and stays under Telegram limit",
      "t_smoketest" in text and len(text) <= 4000)
final = stream._format(final=True, summary="done")
check("_format: final frame shows total", "total" in final)

# 6b. Metric display (v1.0.3): total output tokens, never a tok/s rate —
# the old figure divided cumulative tokens by wall-clock elapsed and read
# as a generation rate. Zero tokens renders no suffix at all.
check("_format: zero tokens renders no token suffix",
      "out tok" not in text and "tok/s" not in text)
tok_stream = mod._TaskStream("t_smoketest")
tok_stream.add_completion_tokens(9412)
tok_text = tok_stream._format()
check("_format: tokens render as thousands-separated total",
      "9,412 out tok" in tok_text and "tok/s" not in tok_text)
tok_final = tok_stream._format(final=True, summary="done")
check("_format: final frame carries the out-tok total",
      "9,412 out tok" in tok_final and "tok/s" not in tok_final)

# 7. steer.py --check exits 1 (not a crash) with no env configured.
env = {k: v for k, v in os.environ.items()
       if not k.startswith("KANBAN_STREAM_")}
proc = subprocess.run([sys.executable, STEER, "--check"],
                      capture_output=True, text=True, env=env, timeout=20)
check("steer.py --check fails clean without env",
      proc.returncode == 1 and "SELF-CHECK: FAILED" in proc.stdout,
      "rc=%s out=%s" % (proc.returncode, proc.stdout[-200:]))

print("\n%s (%d failure(s))" % ("SMOKE OK" if not failures else "SMOKE FAILED",
                                len(failures)))
sys.exit(1 if failures else 0)
