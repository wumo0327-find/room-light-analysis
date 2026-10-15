"""Bounded, cancellable execution shared by the production engines."""
from contextvars import ContextVar
import subprocess
import time

checkpoint = ContextVar('engine_checkpoint', default=None)


def run_process(command, *, cwd=None, env=None, input_text=None, timeout=1800):
    callback = checkpoint.get()
    if callback and callback() is False:
        raise InterruptedError('计算已取消')
    flags = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
    process = subprocess.Popen(command, cwd=cwd, env=env, stdin=subprocess.PIPE,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               creationflags=flags)
    started = time.monotonic()
    payload = input_text.encode('utf-8') if input_text is not None else None
    try:
        while True:
            try:
                stdout, stderr = process.communicate(input=payload, timeout=.25)
                break
            except subprocess.TimeoutExpired:
                payload = None
                if callback and callback() is False:
                    raise InterruptedError('计算已取消')
                if time.monotonic() - started > timeout:
                    raise TimeoutError('计算超过时限：' + str(command[0]))
        if process.returncode:
            raise RuntimeError(str(command[0]) + '\n' + stderr.decode('utf-8', errors='replace')[-4000:])
        return stdout
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate()
