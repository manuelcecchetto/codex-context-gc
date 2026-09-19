#!/usr/bin/env python3
"""Synchronize stock runtime Stop/PostCompact hooks with the GC adapter."""
import json
import socket
import sys


def main():
    payload = json.load(sys.stdin)
    with socket.socket(socket.AF_UNIX) as client:
        client.settimeout(30)
        client.connect(sys.argv[1])
        client.sendall((json.dumps(payload) + '\n').encode())
        reply = b''
        while b'\n' not in reply:
            part = client.recv(4096)
            if not part:
                raise RuntimeError('GC coordinator disconnected before checkpoint handoff.')
            reply += part
        result = json.loads(reply)
        if not result.get('ok'):
            raise RuntimeError('GC checkpoint injection failed; saved checkpoint retained.')
    print('{}')


if __name__ == '__main__':
    main()
