"""CERT14: write the four AI inputs from a second host, flags omitted, for 6 s."""
import socket, sys, time
host = sys.argv[1]
w = socket.create_connection((host, 3490), timeout=5)
print("connected", w.getsockname(), "->", w.getpeername(), flush=True)
for i in range(24):
    w.sendall(b"PITCH;15.0\nROLL;20.0\nALAT;0.0\nTAS;120.0\n")
    time.sleep(0.25)
for k in ("PITCH", "ROLL", "ALAT", "TAS"):
    w.sendall(f"@r{k}\n".encode()); time.sleep(0.1)
print(w.recv(4096).decode().strip().replace("\n", " | "))
