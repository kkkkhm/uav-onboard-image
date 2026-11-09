"""Shared operations for offline image files."""
import subprocess


def run(*args, allowed=(0,)):
    result = subprocess.run([str(a) for a in args], text=True, capture_output=True)
    print(result.stdout, end="", flush=True)
    if result.stderr:
        print(result.stderr, end="", flush=True)
    if result.returncode not in allowed:
        raise RuntimeError("Command failed: " + str(args))
    return result.stdout


def extract(source, offset, size, destination):
    print("Extracting " + destination.name, flush=True)
    with source.open("rb") as inp, destination.open("xb") as out:
        inp.seek(offset)
        remaining = size
        while remaining:
            data = inp.read(min(8 * 1048576, remaining))
            if not data:
                raise EOFError(source)
            if data.count(0) == len(data):
                out.seek(len(data), 1)
            else:
                out.write(data)
            remaining -= len(data)
        out.truncate(size)
