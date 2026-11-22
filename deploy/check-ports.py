"""Read-only preflight. No fallback to unexpected ports."""
import socket
import sys

ports = [int(value) for value in sys.argv[1:]] or [5173, 8000, 5432]
occupied = []
for port in ports:
    with socket.socket() as connection:
        if connection.connect_ex(('127.0.0.1', port)) == 0:
            occupied.append(port)
if occupied:
    print('Occupied ports:', ', '.join(map(str, occupied)))
    raise SystemExit(1)
print('Requested ports are free:', ', '.join(map(str, ports)))
