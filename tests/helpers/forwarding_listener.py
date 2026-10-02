"""Stand-in for SSH in real supervisor lifecycle tests; no catalog access."""
import socket
import sys
import time

listeners = []
for argument in sys.argv[1:]:
    listener = socket.socket()
    listener.bind(("127.0.0.1", int(argument)))
    listener.listen()
    listeners.append(listener)
while True:
    time.sleep(0.1)
