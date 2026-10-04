"""Host sensors, polled off the frame loop.

nvidia-smi takes tens of milliseconds to start, which would blow the frame
budget on its own, so a daemon thread polls once a second and the render loop
reads its last snapshot.
"""
import os
import subprocess
import threading
import time

#: hwmon driver names whose temp1_input is the CPU package/die temperature.
_CPU_HWMON = ('coretemp', 'k10temp', 'zenpower')


def cpu_temp():
    """Package temperature from hwmon, Intel or AMD."""
    for node in os.listdir('/sys/class/hwmon'):
        path = f'/sys/class/hwmon/{node}'
        try:
            if open(f'{path}/name').read().strip() not in _CPU_HWMON:
                continue
        except OSError:
            continue
        # Prefer an explicitly labelled package/die sensor; fall back to temp1.
        for entry in sorted(os.listdir(path)):
            if not entry.endswith('_label'):
                continue
            try:
                label = open(f'{path}/{entry}').read().strip().lower()
            except OSError:
                continue
            if any(k in label for k in ('package', 'tctl', 'tdie')):
                try:
                    return int(open(f'{path}/{entry[:-6]}_input').read()) / 1000
                except OSError:
                    pass
        try:
            return int(open(f'{path}/temp1_input').read()) / 1000
        except OSError:
            pass
    return None


def cpu_vendor():
    try:
        info = open('/proc/cpuinfo').read()
        return 'intel' if 'GenuineIntel' in info else (
            'amd' if 'AuthenticAMD' in info else None)
    except OSError:
        return None


def _amd_gpu_hwmon():
    for node in os.listdir('/sys/class/hwmon'):
        path = f'/sys/class/hwmon/{node}'
        try:
            if open(f'{path}/name').read().strip() == 'amdgpu':
                return path
        except OSError:
            pass
    return None


def detect_gpu():
    """Return ('nvidia'|'amd'|None, reader) where reader() -> (temp, load)."""
    if subprocess.run(['which', 'nvidia-smi'], capture_output=True).returncode == 0:
        def read_nvidia():
            try:
                out = subprocess.run(
                    ['nvidia-smi', '--query-gpu=temperature.gpu,utilization.gpu',
                     '--format=csv,noheader,nounits'],
                    capture_output=True, text=True, timeout=5).stdout.strip()
                if out:
                    temp, load = out.splitlines()[0].split(',')
                    return float(temp), float(load)
            except Exception:
                pass
            return None, None
        return 'nvidia', read_nvidia

    hwmon = _amd_gpu_hwmon()
    if hwmon:
        # busy_percent lives on the parent DRM device, not in hwmon itself.
        busy = os.path.join(os.path.realpath(hwmon + '/device'), 'gpu_busy_percent')

        def read_amd():
            temp = load = None
            try:
                temp = int(open(hwmon + '/temp1_input').read()) / 1000
            except OSError:
                pass
            try:
                load = float(open(busy).read().strip())
            except OSError:
                pass
            return temp, load
        return 'amd', read_amd

    return None, lambda: (None, None)


class Sensors(threading.Thread):
    daemon = True

    def __init__(self, interval=1.0):
        super().__init__(daemon=True)
        self.interval = interval
        self.lock = threading.Lock()
        self.cpu_temp = self.cpu_load = self.gpu_temp = self.gpu_load = None
        self.gpu_vendor, self._read_gpu = detect_gpu()
        self.cpu_vendor = cpu_vendor()
        self._prev = self._read_stat()
        self.stop_requested = False

    @staticmethod
    def _read_stat():
        try:
            values = [int(x) for x in open('/proc/stat').readline().split()[1:]]
            return sum(values), values[3] + values[4]      # total, idle + iowait
        except (OSError, IndexError, ValueError):
            return None

    def run(self):
        while not self.stop_requested:
            temp = cpu_temp()
            load = None
            current = self._read_stat()
            if current and self._prev:
                d_total = current[0] - self._prev[0]
                d_idle = current[1] - self._prev[1]
                if d_total > 0:
                    load = max(0.0, min(100.0, 100.0 * (d_total - d_idle) / d_total))
            self._prev = current
            gpu_temp, gpu_load = self._read_gpu()
            with self.lock:
                self.cpu_temp, self.cpu_load = temp, load
                self.gpu_temp, self.gpu_load = gpu_temp, gpu_load
            time.sleep(self.interval)

    def stop(self):
        self.stop_requested = True

    def snapshot(self):
        with self.lock:
            return self.cpu_temp, self.cpu_load, self.gpu_temp, self.gpu_load
