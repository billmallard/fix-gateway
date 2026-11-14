echo LOCKED $(date -u +%FT%TZ)
systemctl --user show fixgw.service -p MainPID -p ActiveEnterTimestamp
systemctl --user show pyefis.service -p MainPID -p ActiveEnterTimestamp
systemctl --user is-active pyefis.service
git -C ~/fix-gateway rev-parse HEAD; git -C ~/pyEfis rev-parse HEAD
grep -n "host:" ~/makerplane/fixgw/config/connections/netfix.yaml
ss -ltn | grep 3490
ss -tnp | grep :3490
echo READY; sleep 12
echo LOCALREAD
python3 - <<'PY'
import socket, time
s = socket.create_connection(("127.0.0.1", 3490), timeout=3)
s.sendall(b"@rPITCH\n"); time.sleep(0.3); print("loopback:", s.recv(999).decode().strip())
PY
echo END $(date -u +%FT%TZ)
systemctl --user show fixgw.service -p MainPID
