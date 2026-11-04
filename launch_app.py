"""Open one persistent local app instance; safe to double-click repeatedly."""
import argparse
import errno
import json
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.request import ProxyHandler, Request, build_opener
import webbrowser

ROOT = Path(__file__).resolve().parent


def probe(url, root=ROOT):
    """Distinguish our workspace from another service without touching it."""
    # urllib may wrap localhost refusal in a platform-specific reason object
    # (or consult proxy settings). A direct loopback connect reliably tells us
    # whether a listener exists before we make the health request.
    parts=url.removeprefix('http://').split('/',1)[0].rsplit(':',1)
    if len(parts)!=2:return 'unresponsive'
    host,port=parts
    try:
        connection=socket.create_connection((host,int(port)),timeout=1)
    except OSError as exc:
        # A sandbox or macOS privacy restriction may deny the raw socket probe
        # even when urllib can still reach the local server. Only an explicit
        # refusal proves the port is offline; for other errors, try the health
        # endpoint below before refusing to launch.
        if exc.errno == errno.ECONNREFUSED:
            return 'offline'
    else:
        connection.close()
    try:
        request = Request(url + '/api/health', headers={'Accept': 'application/json'})
        with build_opener(ProxyHandler({})).open(request, timeout=1) as response:
            health = json.loads(response.read(4096))
        return 'ready' if health == {'app': 'pcb-preflight', 'workspace': str(root)} else 'occupied'
    except HTTPError:
        return 'occupied'
    except URLError as exc:
        return 'offline' if isinstance(exc.reason, ConnectionRefusedError) else 'unresponsive'
    except (TimeoutError, OSError):
        return 'unresponsive'
    except (ValueError, TypeError):
        return 'occupied'


def launch(port=8765, open_browser=True, root=ROOT):
    url = f'http://127.0.0.1:{port}'
    try:
        free = shutil.disk_usage(root).free
    except OSError:
        free = None
    if free is not None and free < 5*1024**3:
        print(f'Warning: only {free/1024**3:.1f} GB of disk space is free. '
              'Free space before running a check; reviews cannot be saved or loaded on a full disk.')
    status = probe(url, root)
    if status not in ('ready', 'offline'):
        print(f'Port {port} is in use or not responding. No existing process was stopped. Try another port with --port {port + 1}.')
        return 1
    if status == 'offline':
        reports = root / 'reports'
        reports.mkdir(exist_ok=True)
        log = reports / '.webserver.log'
        try:
            with log.open('ab') as output:
                process = subprocess.Popen(
                    [sys.executable, str(root / 'main.py'), '--web', '--no-browser', '--port', str(port)],
                    cwd=root, stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.STDOUT,
                    start_new_session=True, close_fds=True)
        except OSError as exc:
            print(f'Could not start PCB Preflight: {exc}')
            return 1
        print('Starting PCB Preflight…', flush=True)
        # First import of the KiCad/report stack can be slow on macOS, and
        # opening a saved review may add more startup time. Do not tell the user
        # the app failed while that local initialization is still progressing.
        for _ in range(450):
            status = probe(url, root)
            if status == 'ready':
                break
            if process.poll() is not None or status == 'occupied':
                print(f'The app could not start. Details: {log}')
                return 1
            time.sleep(.2)
        else:
            print(f'The app is taking longer to start. Open {url} again shortly. Details: {log}')
            return 1
    print(f'PCB Preflight is ready: {url}\nYou can close this window. The app keeps running.', flush=True)
    if open_browser:
        webbrowser.open(url)
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--no-browser', action='store_true')
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error('--port must be between 1 and 65535.')
    return launch(args.port, not args.no_browser)


if __name__ == '__main__':
    raise SystemExit(main())
