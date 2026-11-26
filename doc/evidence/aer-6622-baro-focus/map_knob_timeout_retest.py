import os, socket, subprocess, time
env = dict(os.environ, DISPLAY=":0")
def grab(n):
    subprocess.run(["ffmpeg","-loglevel","error","-y","-f","x11grab","-draw_mouse","0","-video_size","1920x1080","-i",":0","-frames:v","1","aer6622-knob/frame-%s.png"%n],env=env,check=True)
s = socket.create_connection(("127.0.0.1", 3490), 5); f = s.makefile("rb")
def read(k):
    s.sendall(("@r%s\n"%k).encode())
    while True:
        l=f.readline().decode().strip()
        if l.startswith("@r%s;"%k): return l
print(read("ENC3"), read("MAPRANGE"))
grab("7-retest-before")
p=os.open("/dev/tessera",os.O_WRONLY|os.O_NOCTTY); os.write(p,b"AD_PRESS 10\n"); os.close(p)
time.sleep(1.0); grab("8-retest-focused")
p=os.open("/dev/tessera",os.O_WRONLY|os.O_NOCTTY); os.write(p,b"AD_PRESS 10\n"); os.close(p)
time.sleep(0.5)
s.sendall(b"BTN3;1;0\n"); time.sleep(0.2); s.sendall(b"BTN3;0;0\n")   # push: snap 8 -> 10
time.sleep(12.0); grab("9-retest-timed-out")
print(read("ENC3"), read("MAPRANGE"))
s.close()
