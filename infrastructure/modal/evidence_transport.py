"""Bounded, repeatable reads of benchmark evidence."""
import hashlib
import json
from pathlib import Path
import socket
import time

import aiohttp
from modal import exception as modal_error

MAX_WORKER_RESULT = 82 * 1024 * 1024
WORKER_ARTIFACT = "worker-result.pkl"
WORKER_MANIFEST = "worker-result.json"
READ_DELAYS = (1, 2, 4)


def transient_read_failure(error):
    if isinstance(error, (modal_error.FunctionTimeoutError, modal_error.NotFoundError, aiohttp.ClientSSLError)):
        return False
    if isinstance(error, (socket.gaierror, TimeoutError, ConnectionError, modal_error.ConnectionError,
                          aiohttp.ClientConnectionError, aiohttp.ClientPayloadError)):
        return True
    status = getattr(getattr(error, "status", None), "name", None)
    return status in ("UNAVAILABLE", "DEADLINE_EXCEEDED", "RESOURCE_EXHAUSTED")


def retry_read(read):
    for delay in (*READ_DELAYS, None):
        try:
            return read()
        except Exception as error:
            if delay is None or not transient_read_failure(error):
                raise
            time.sleep(delay)


def wait_for_worker(call, timeout):
    """Poll one saved invocation. A lost response must never resubmit its workload."""
    deadline = time.monotonic() + timeout
    failures = 0
    while (remaining := deadline - time.monotonic()) > 0:
        try:
            return call.get(timeout=min(15, remaining))
        except modal_error.FunctionTimeoutError:
            raise
        except (modal_error.TimeoutError, TimeoutError):
            failures = 0
        except Exception as error:
            if not transient_read_failure(error):
                raise
            delay = READ_DELAYS[min(failures, len(READ_DELAYS) - 1)]
            failures += 1
            time.sleep(min(delay, max(0, deadline - time.monotonic())))
    raise TimeoutError("Timed out waiting for the original generator invocation")


def checkpoint_worker(volume, directory, workload):
    directory = Path(directory)
    # A replacement container must never replay the workload after mutations may have started.
    with (directory / "started").open("x"):
        pass
    retry_read(volume.commit)
    payload = workload()
    if not isinstance(payload, bytes) or not 0 < len(payload) <= MAX_WORKER_RESULT:
        raise ValueError("Invalid generator evidence payload")
    manifest = {"size": len(payload), "sha256": hashlib.sha256(payload).hexdigest()}
    for name, data in ((WORKER_ARTIFACT, payload), (WORKER_MANIFEST, json.dumps(manifest).encode())):
        destination = directory / name
        staging = destination.with_name(name + ".tmp")
        staging.write_bytes(data)
        staging.replace(destination)
    retry_read(volume.commit)
    return manifest


def read_worker_artifact(volume, manifest, prefix=""):
    if prefix not in ("", "warmup/", "measure/", "warmup-recovery/", "measure-recovery/"): raise ValueError("Invalid evidence prefix")
    if not isinstance(manifest, dict):
        raise ValueError("Invalid generator evidence manifest")
    size = manifest.get("size")
    digest = manifest.get("sha256")
    if type(size) is not int or not 0 < size <= MAX_WORKER_RESULT or not isinstance(digest, str) or len(digest) != 64:
        raise ValueError("Invalid generator evidence manifest")

    def read():
        payload = bytearray()
        for chunk in volume.read_file("/" + prefix + WORKER_ARTIFACT):
            if not isinstance(chunk, bytes) or len(payload) + len(chunk) > size:
                raise ValueError("Generator evidence exceeded its manifest size")
            payload.extend(chunk)
        if len(payload) != size or hashlib.sha256(payload).hexdigest() != digest:
            raise ValueError("Generator evidence does not match its manifest")
        return payload

    return retry_read(read)


def read_worker_manifest(volume, prefix=""):
    if prefix not in ("", "warmup/", "measure/", "warmup-recovery/", "measure-recovery/"): raise ValueError("Invalid evidence prefix")
    def read():
        payload = bytearray()
        for chunk in volume.read_file("/" + prefix + WORKER_MANIFEST):
            if len(payload) + len(chunk) > 4096:
                raise ValueError("Generator evidence manifest exceeded its bound")
            payload.extend(chunk)
        return json.loads(payload)

    return retry_read(read)
