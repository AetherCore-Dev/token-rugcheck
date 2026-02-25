"""Runner: launch _diag_full.py in subprocess, capture streaming output."""
import asyncio
import os

async def run():
    proc = await asyncio.create_subprocess_exec(
        "/opt/miniconda3/bin/python", "-u", "tests/_diag_full.py",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        env={**os.environ, "PYTHONUNBUFFERED": "1", "X402_MODE": "test", "X402_NETWORK": "mock"},
    )
    # Stream output line by line
    lines = []
    try:
        async def read_lines():
            while True:
                line = await proc.stdout.readline()
                if not line:
                    break
                decoded = line.decode().rstrip()
                lines.append(decoded)
                print(decoded, flush=True)

        await asyncio.wait_for(read_lines(), timeout=75)
    except asyncio.TimeoutError:
        print(f"\n=== TIMEOUT after 75s, got {len(lines)} lines ===", flush=True)
        proc.kill()
    
    await proc.wait()
    print(f"exit_code={proc.returncode}", flush=True)

asyncio.run(run())
