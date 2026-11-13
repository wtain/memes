"""A fake ssh for the tunnel tests: first argument is the mode, the rest is the real ssh argument list."""
import socket
import sys
import time

mode, args = sys.argv[1], sys.argv[2:]
if mode == "fail":
    sys.stderr.write("Permission denied (publickey).\n")
    sys.exit(255)
if mode == "hang":  # alive but never listens
    time.sleep(60)
    sys.exit(0)
spec = args[args.index("-L") + 1]
host, port = spec.split(":")[0], int(spec.split(":")[1])
server = socket.socket()
server.bind((host, port))
server.listen(5)
while True:
    conn, _ = server.accept()
    conn.close()
