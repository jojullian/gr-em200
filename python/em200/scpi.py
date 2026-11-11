"""Conexión SCPI (TCP 5555) y socket de trazas TCP (puerto SCPI + 10) del EM200."""
import socket
import threading


class Scpi:
    def __init__(self, ip, port=5555, timeout=5.0):
        self.sock = socket.create_connection((ip, port), timeout=timeout)
        self.sock.settimeout(timeout)
        self._rx = b""
        self.lock = threading.Lock()

    def write(self, cmd):
        with self.lock:
            self.sock.sendall((cmd + "\n").encode())

    def query(self, cmd):
        with self.lock:
            self.sock.sendall((cmd + "\n").encode())
            while b"\n" not in self._rx:
                chunk = self.sock.recv(4096)
                if not chunk:
                    raise ConnectionError("el EM200 cerró la conexión SCPI")
                self._rx += chunk
            line, self._rx = self._rx.split(b"\n", 1)
            return line.decode(errors="replace").strip()

    def errors(self, max_errors=10):
        """Vacía la cola de errores del equipo y devuelve la lista (vacía si no hay)."""
        out = []
        for _ in range(max_errors):
            err = self.query("SYST:ERR?")
            if err.startswith("0,") or err.startswith("+0,"):
                break
            out.append(err)
        return out

    def close(self):
        try:
            self.sock.close()
        except OSError:
            pass


def open_trace_socket(ip, scpi_port=5555, rcvbuf=16 * 1024 * 1024):
    """Abre el socket de datos TCP del EM200 y arranca el keep-alive (manual 7.3)."""
    s = socket.create_connection((ip, scpi_port + 10), timeout=5.0)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, rcvbuf)
    s.settimeout(0.5)
    s.sendall(b"\x00")
    return s
