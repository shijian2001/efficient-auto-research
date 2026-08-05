from __future__ import annotations

import argparse
import asyncio


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--listen-host", required=True)
    parser.add_argument("--listen-port", type=int, required=True)
    parser.add_argument("--target-host", required=True)
    parser.add_argument("--target-port", type=int, required=True)
    return parser.parse_args()


async def copy_stream(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
) -> None:
    try:
        while data := await reader.read(1024 * 1024):
            writer.write(data)
            await writer.drain()
    finally:
        writer.close()
        await writer.wait_closed()


async def handle_connection(
    client_reader: asyncio.StreamReader,
    client_writer: asyncio.StreamWriter,
    target_host: str,
    target_port: int,
) -> None:
    try:
        target_reader, target_writer = await asyncio.open_connection(
            target_host,
            target_port,
        )
    except Exception:
        client_writer.close()
        await client_writer.wait_closed()
        return
    await asyncio.gather(
        copy_stream(client_reader, target_writer),
        copy_stream(target_reader, client_writer),
        return_exceptions=True,
    )


async def main() -> None:
    args = parse_args()
    server = await asyncio.start_server(
        lambda reader, writer: handle_connection(
            reader,
            writer,
            args.target_host,
            args.target_port,
        ),
        args.listen_host,
        args.listen_port,
    )
    addresses = ", ".join(str(socket.getsockname()) for socket in server.sockets or [])
    print(
        f"Proxy bridge listening on {addresses}; forwarding to "
        f"{args.target_host}:{args.target_port}",
        flush=True,
    )
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    asyncio.run(main())
