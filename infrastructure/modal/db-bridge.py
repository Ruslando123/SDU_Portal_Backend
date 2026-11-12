"""Bounded TCP forwarding over an authenticated Modal TLS WebSocket."""
import asyncio
import hmac
from http import HTTPStatus
import os
import re
import sys

from websockets.legacy.client import connect
from websockets.legacy.server import serve

KEY = os.environ["SDU_DB_BRIDGE_KEY"]
PATH = "/database"
FRAME = 32768
LIMIT = 64
MAX_BYTES = 768 * 1024 * 1024


async def relay(reader, writer, websocket, account=lambda _: True):
    async def outgoing():
        while chunk := await reader.read(FRAME):
            if not account(len(chunk)):
                break
            await websocket.send(chunk)

    async def incoming():
        async for chunk in websocket:
            if not isinstance(chunk, bytes):
                await websocket.close(1003)
                break
            if not account(len(chunk)):
                break
            writer.write(chunk)
            await writer.drain()

    tasks = [asyncio.create_task(action()) for action in (outgoing, incoming)]
    try:
        await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


async def server():
    slots = asyncio.Semaphore(LIMIT)
    total = 0

    def account(size):
        nonlocal total
        total = min(total + size, MAX_BYTES + 1)
        return total <= MAX_BYTES

    async def authenticate(path, headers):
        keys = headers.get_all("X-SDU-DB-Bridge-Key")
        if path != PATH or len(keys) != 1 or not hmac.compare_digest(KEY.encode(), keys[0].encode()):
            return HTTPStatus.FORBIDDEN, [("Content-Type", "text/plain")], b"Forbidden\n"
        return None

    async def handler(websocket):
        if slots.locked():
            await websocket.close(1013)
            return
        async with slots:
            reader, writer = await asyncio.open_connection("127.0.0.1", 5432)
            try:
                await relay(reader, writer, websocket, account)
            finally:
                writer.close()
                await writer.wait_closed()

    async with serve(handler, "0.0.0.0", 8090, process_request=authenticate, max_size=FRAME,
                     max_queue=8, compression=None, close_timeout=1):
        await asyncio.Future()


async def client():
    host = os.environ["SDU_DB_BRIDGE_HOST"]
    if not re.fullmatch(r"[a-z0-9-]+(?:\.[a-z0-9-]+)*\.modal\.(host|run)", host):
        raise ValueError("Unexpected Modal database tunnel hostname")
    slots = asyncio.Semaphore(LIMIT)

    async def handler(reader, writer):
        try:
            if slots.locked():
                return
            async with slots:
                async with connect(f"wss://{host}{PATH}", extra_headers={"X-SDU-DB-Bridge-Key": KEY},
                                   max_size=FRAME, max_queue=8, compression=None, open_timeout=4,
                                   close_timeout=1) as websocket:
                    await relay(reader, writer, websocket)
        except (OSError, asyncio.TimeoutError):
            pass
        finally:
            writer.close()
            await writer.wait_closed()

    async with await asyncio.start_server(handler, "127.0.0.1", 15432, backlog=LIMIT) as listener:
        await listener.serve_forever()


if __name__ == "__main__":
    asyncio.run({"server": server, "client": client}[sys.argv[1]]())
