import multiprocessing
import socket
import threading
import time

HOSTNAME = "tt"


def get_local_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


def _run_broadcaster(port, stop_event):
    """Entry point for the child process. Confirmed by testing: a network
    interface change (Wi-Fi reconnect, DHCP release/renew) can make
    zeroconf's Windows networking calls block for ~30s in a way that holds
    the GIL, freezing every thread in the process -- including Waitress's,
    with no exception ever raised to catch. Running this in its own process
    means that freeze is contained here; the main Flask/Waitress process has
    its own GIL and is never affected, no matter what this one does.
    """
    from zeroconf import Zeroconf, ServiceInfo

    while not stop_event.is_set():
        zc = None
        try:
            ip = get_local_ip()
            info = ServiceInfo(
                "_http._tcp.local.",
                "TimeTracker._http._tcp.local.",
                addresses=[socket.inet_aton(ip)],
                port=port,
                server=f"{HOSTNAME}.local.",
            )
            zc = Zeroconf()
            zc.register_service(info)
            stop_event.wait()
        except Exception:
            # A genuine (non-hang) failure here -- back off and retry rather
            # than leaving the broadcast permanently dead for the rest of
            # the session.
            if zc is not None:
                try:
                    zc.close()
                except Exception:
                    pass
            if stop_event.wait(timeout=5):
                break
            continue
        finally:
            if zc is not None:
                try:
                    zc.close()
                except Exception:
                    pass


class MdnsBroadcaster:
    """Supervises the mDNS child process: starts it, restarts it (with a
    cooldown) if it ever exits unexpectedly, and can force-terminate it on
    shutdown even if it's wedged in a blocking call."""

    def __init__(self, port, restart_cooldown=10):
        self._port = port
        self._restart_cooldown = restart_cooldown
        self._stop_event = multiprocessing.Event()
        self._process = None
        self._monitor_thread = None
        self._stopping = False

    def _spawn(self):
        self._process = multiprocessing.Process(
            target=_run_broadcaster, args=(self._port, self._stop_event), daemon=True
        )
        self._process.start()

    def _monitor(self):
        while not self._stopping:
            time.sleep(1)
            if self._stopping or self._stop_event.is_set():
                return
            if self._process is not None and not self._process.is_alive():
                time.sleep(self._restart_cooldown)
                if self._stopping or self._stop_event.is_set():
                    return
                try:
                    self._spawn()
                except Exception:
                    pass

    def start(self):
        self._spawn()
        self._monitor_thread = threading.Thread(target=self._monitor, daemon=True)
        self._monitor_thread.start()

    def stop(self):
        self._stopping = True
        self._stop_event.set()
        if self._process is not None:
            self._process.join(timeout=2)
            if self._process.is_alive():
                self._process.terminate()
                self._process.join(timeout=2)
