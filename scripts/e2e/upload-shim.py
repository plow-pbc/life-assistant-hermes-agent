#!/usr/bin/env python3
"""Make the twin's browser-facing upload origin reachable from inside the agent.

Plow's media contract hands the client an `upload_url` it must PUT to with the
returned headers and nothing else -- that URL is a write capability, not a Plow
endpoint, so the plugin cannot rewrite it. Locally the twin fills it from
`LINQ_TWIN_UPLOAD_BASE_URL`, which is `http://localhost:<host port>`: right for
a browser on the Mac, unreachable inside a container, where it fails with
`Cannot connect to host localhost:<port>` and the image never leaves.

Rather than reconfigure the shared stack, this forwards that exact address
inside the container to the twin's host port. Two listeners, because the URL's
`localhost` resolves to both stacks on this image.
"""
import asyncio
import os
import sys

PORT = int(os.environ["TWIN_UPLOAD_PORT"])
# host.docker.internal, not configurable: it is how a container reaches the Mac
# under both OrbStack and Docker Desktop, which is every runtime this loop
# supports. The override that used to be here was never set by anything.
TARGET_HOST = "host.docker.internal"


async def pipe(reader, writer):
    try:
        while data := await reader.read(65536):
            writer.write(data)
            await writer.drain()
    except (ConnectionError, asyncio.IncompleteReadError):
        pass
    finally:
        writer.close()


async def handle(local_reader, local_writer):
    try:
        remote_reader, remote_writer = await asyncio.open_connection(TARGET_HOST, PORT)
    except OSError as exc:
        print(f"upload-shim: cannot reach {TARGET_HOST}:{PORT}: {exc}", file=sys.stderr)
        local_writer.close()
        return
    await asyncio.gather(pipe(local_reader, remote_writer), pipe(remote_reader, local_writer))


async def main():
    servers = await asyncio.start_server(handle, ["127.0.0.1", "::1"], PORT)
    print(f"upload-shim: 127.0.0.1:{PORT} -> {TARGET_HOST}:{PORT}", file=sys.stderr)
    async with servers:
        await servers.serve_forever()


asyncio.run(main())
