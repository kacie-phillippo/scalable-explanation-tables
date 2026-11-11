"""Passive run measurements and append-only experiment reports (standard library)."""
from __future__ import annotations

import ctypes
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import sys
import threading


def _rss_reader():
    if os.name == 'nt':
        from ctypes import wintypes

        class Counters(ctypes.Structure):
            _fields_ = [('cb', wintypes.DWORD), ('PageFaultCount', wintypes.DWORD)] + [
                (name, ctypes.c_size_t) for name in (
                    'PeakWorkingSetSize', 'WorkingSetSize', 'QuotaPeakPagedPoolUsage',
                    'QuotaPagedPoolUsage', 'QuotaPeakNonPagedPoolUsage',
                    'QuotaNonPagedPoolUsage', 'PagefileUsage', 'PeakPagefileUsage')]

        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.GetCurrentProcess.restype = wintypes.HANDLE
        psapi = ctypes.WinDLL('psapi', use_last_error=True)
        psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD]
        psapi.GetProcessMemoryInfo.restype = wintypes.BOOL

        def read(child=None):
            handle = kernel.GetCurrentProcess() if child is None else int(child._handle)
            values = Counters()
            values.cb = ctypes.sizeof(values)
            if not psapi.GetProcessMemoryInfo(handle, ctypes.byref(values), values.cb):
                return None
            return values.WorkingSetSize, values.PeakWorkingSetSize
        return read
    if sys.platform.startswith('linux'):
        def read(child=None):
            try:
                pid = os.getpid() if child is None else child.pid
                fields = dict(line.split(':', 1) for line in Path(f'/proc/{pid}/status').read_text().splitlines() if ':' in line)
                return tuple(int(fields[key].split()[0]) * 1024 for key in ('VmRSS', 'VmHWM'))
            except (OSError, KeyError, ValueError):
                return None
        return read
    return lambda child=None: None


class MemoryMonitor:
    """Sample the combined resident memory of Python and the explanation engine."""
    interval_seconds = .01

    def __init__(self):
        self.read = _rss_reader()
        self.child = None
        self.peak = None
        self.python_peak = None
        self.engine_peak = None
        self.samples = 0
        self.stop_event = threading.Event()
        self.thread = threading.Thread(target=self._watch, daemon=True)
        self.started_utc = datetime.now(timezone.utc).isoformat()

    def sample(self):
        parent = self.read()
        if parent is None:
            return
        child = self.child
        child_memory = self.read(child) if child is not None else None
        self.python_peak = max(self.python_peak or 0, parent[1])
        if child_memory:
            self.engine_peak = max(self.engine_peak or 0, child_memory[1])
        combined = parent[0] + (child_memory[0] if child_memory else 0)
        self.peak = max(self.peak or 0, combined)
        self.samples += 1

    def _watch(self):
        while not self.stop_event.wait(self.interval_seconds):
            self.sample()

    def __enter__(self):
        self.sample()
        self.thread.start()
        return self

    def stop(self):
        if not self.stop_event.is_set():
            self.stop_event.set()
            self.thread.join()
            self.sample()

    def __exit__(self, *args):
        self.stop()

    def report(self):
        return {
            'peak_memory_bytes': self.peak,
            'peak_memory_mib': self.peak / 1024**2 if self.peak is not None else None,
            'python_process_lifetime_peak_bytes': self.python_peak,
            'explanation_process_peak_bytes': self.engine_peak,
            'memory_samples': self.samples,
            'memory_sample_interval_seconds': self.interval_seconds,
        }


def save_report(output, manifest, prepared, nodes, telemetry, monitor, compilation_seconds, report_root=None):
    """Save run summaries and table outputs after the measured pipeline has stopped."""
    n = len(prepared['rows'])
    covered = set()
    selected = []
    for row_number, row in enumerate(telemetry['selected_rows'], 1):
        node = nodes[row['candidate_index']]
        covered.update(node.tidset)
        selected.append(dict(row, row=row_number, support_count=len(node.tidset),
                             support_fraction=len(node.tidset)/n))
    if len(selected) != manifest['selected_patterns']:
        raise ValueError('Report trace disagrees with explanation table row count')
    mining = manifest.pop('_mining_counters')
    engine = telemetry['counters']
    scans = {f'mining_{key}': value for key, value in mining.items() if key.endswith('_scans')}
    scans.update({f'explanation_{key}': value for key, value in engine.items() if key.endswith('_scans')})
    supports = [row['support_count'] for row in selected]
    average = sum(supports)/len(supports) if supports else None
    summary = {
        'dataset': Path(manifest['data']).name,
        'has_hierarchies': bool(prepared['db'][3]),
        'minwus': manifest['minwus'], 'k': manifest['maximum_rows'],
        'actual_rows': len(selected) + 1, 'selected_patterns': len(selected),
        'k_includes_baseline': True,
        'row_count_semantics': 'total_table_rows_including_baseline',
        'mining_seconds': manifest['mining_seconds'],
        'explanation_seconds': manifest['explanation_seconds'],
        'whole_system_seconds': manifest['elapsed_seconds'],
        'peak_memory_mib': monitor.peak / 1024**2 if monitor.peak is not None else None,
        'candidate_patterns': manifest['mined_patterns'],
        'support_calculations': mining.get('support_calculations', 0),
        'tidset_intersections': mining.get('tidset_intersections', 0),
        'full_dataset_scans': sum(scans.values()), 'full_dataset_joins': 0,
        'candidate_evaluations': engine.get('candidate_evaluations', 0),
        'total_information_gain': sum(row['information_gain'] for row in selected),
        'average_support_count': average,
        'coverage_percent': 100*len(covered)/n,
    }
    root = Path(report_root) if report_root is not None else output.parent/'reports'
    root.mkdir(parents=True, exist_ok=True)
    number = max((int(p.name) for p in root.iterdir() if p.is_dir() and p.name.isdecimal()), default=0)+1
    while True:
        destination = root/f'{number:06d}'
        try:
            destination.mkdir()  # Atomic reservation; concurrent runs cannot overwrite.
            break
        except FileExistsError:
            number += 1
    summary = {'report_number': number, **summary}
    report = summary
    (destination/'report.json').write_text(json.dumps(report, indent=2, allow_nan=False), encoding='utf-8')
    for name in ('explanations.csv', 'explanations.png'):
        shutil.copy2(output/name, destination/name)
    manifest['report_directory'] = str(destination.resolve())
    print(f'Saved: {destination.resolve()}', flush=True)
    return destination
