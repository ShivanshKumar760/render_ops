import os
import resource
import shutil
import subprocess
import tempfile

from flask import Flask, request, jsonify, render_template




app = Flask(__name__)

# ---------------------------------------------------------------------------
# IMPORTANT: Render does not allow a running service to spawn its own Docker
# containers (no Docker socket, no --privileged, no Docker-in-Docker -- this
# is confirmed by Render's own support team). So this is NOT "one fresh
# container per submission." Instead, each submission runs as a short-lived
# subprocess inside this same container, constrained with OS-level resource
# limits (CPU time, memory, process count, output size) and a hard timeout.
#
# This stops accidental problems well (infinite loops, memory blowups, fork
# bombs) but it is NOT a hard security boundary against a determined
# malicious user -- code still runs as a real OS process that can, e.g.,
# `import os` and touch this container's filesystem. Do not expose this to
# untrusted public traffic without a stronger sandbox (see GUIDE.md).
# ---------------------------------------------------------------------------

TIMEOUT_SECONDS = 5
CPU_TIME_LIMIT = 5                    # seconds of CPU time
MEMORY_LIMIT_BYTES = 100 * 1024 * 1024  # 100 MB address space
MAX_OUTPUT_BYTES = 1 * 1024 * 1024     # 1 MB stdout/stderr cap
MAX_PROCESSES = 10                     # blocks basic fork bombs


def apply_limits():
    """Runs in the child process (after fork, before exec) to cap what the
    submitted code can do. Runs as a separate step from the parent Flask
    process, so a crash or limit hit here only kills the child."""
    resource.setrlimit(resource.RLIMIT_CPU, (CPU_TIME_LIMIT, CPU_TIME_LIMIT))
    resource.setrlimit(resource.RLIMIT_AS, (MEMORY_LIMIT_BYTES, MEMORY_LIMIT_BYTES))
    resource.setrlimit(resource.RLIMIT_NPROC, (MAX_PROCESSES, MAX_PROCESSES))
    resource.setrlimit(resource.RLIMIT_FSIZE, (MAX_OUTPUT_BYTES, MAX_OUTPUT_BYTES))


@app.route("/")
def health():
    return jsonify(status="ok", service="code-runner")

@app.route("/home")
def home():
    return render_template("index.html")


@app.route("/execute", methods=["POST"])
def execute():
    data = request.get_json(silent=True) or {}
    code = data.get("code")

    if not code or not isinstance(code, str):
        return jsonify(error="Missing 'code' (string)"), 400

    tmp_dir = tempfile.mkdtemp(prefix="run_")
    script_path = os.path.join(tmp_dir, "script.py")

    try:
        with open(script_path, "w") as f:
            f.write(code)

        result = subprocess.run(
            ["python3", "-I", "script.py"],   # -I: isolated mode (ignores user env/site dirs)
            cwd=tmp_dir,
            capture_output=True,
            text=True,
            timeout=TIMEOUT_SECONDS,
            preexec_fn=apply_limits,
            env={"PATH": "/usr/bin:/bin"},      # minimal env, no secrets leak in
        )
        return jsonify(
            stdout=result.stdout[-MAX_OUTPUT_BYTES:],
            stderr=result.stderr[-MAX_OUTPUT_BYTES:],
            exit_code=result.returncode,
        )
    except subprocess.TimeoutExpired:
        return jsonify(error=f"Execution timed out after {TIMEOUT_SECONDS}s"), 408
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)