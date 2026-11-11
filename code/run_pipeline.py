"""Build an explanation table from CSV inputs and save CSV, PNG, and JSON outputs."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import struct
import time
import tempfile

from mining import mine_fwuis
from prepare import prepare
from render import read_table, render_table
from reporting import MemoryMonitor, save_report

DIRECTORY = Path(__file__).resolve().parent


def write_csv(path, header, rows, delimiter=',', line_ending='\r\n'):
    with Path(path).open('w', encoding='utf-8', newline='') as handle:
        writer = csv.writer(handle, delimiter=delimiter, lineterminator=line_ending)
        writer.writerow(header)
        writer.writerows(rows)


def compiler_path():
    compiler = os.environ.get('CXX') or shutil.which('g++')
    if not compiler and Path('C:/msys64/ucrt64/bin/g++.exe').is_file():
        compiler = 'C:/msys64/ucrt64/bin/g++.exe'
    if not compiler:
        raise ValueError('C++ compiler not found. Install g++ and Boost headers, or set CXX to the compiler executable.')
    return compiler


def compile_engine():
    compiler = compiler_path()
    flags = ('-O2',)
    build = DIRECTORY/'build'
    build.mkdir(exist_ok=True)
    engine = DIRECTORY/'engine'
    sources = sorted(engine.iterdir())
    compiler_identity = '\0'.join((str(compiler), *flags)).encode()
    signature = hashlib.sha256(compiler_identity + b''.join(p.read_bytes() for p in sources if p.is_file())).hexdigest()
    binary = build/('explanations.exe' if os.name == 'nt' else 'explanations')
    stamp = build/'source.sha256'
    env = os.environ.copy()
    env['PATH'] = str(Path(compiler).resolve().parent) + os.pathsep + env.get('PATH', '')
    if not binary.exists() or not stamp.exists() or stamp.read_text() != signature:
        result = subprocess.run([compiler, *flags, str(engine/'main.cpp'), str(engine/'Lighthouse.cpp'), '-o', str(binary)], capture_output=True, text=True, env=env)
        (build/'compile.log').write_text(result.stdout + result.stderr, encoding='utf-8')
        if result.returncode:
            raise ValueError(f'C++ compilation failed; see {build / "compile.log"}\n{result.stderr}')
        stamp.write_text(signature)
    return binary, env


def run(data_path, hierarchy_path, minwus, max_rows, output, *, keep_intermediates=False,
        presence_only_binary=False):
    report_root = Path(output).parent/'reports'
    with MemoryMonitor() as monitor:
        if keep_intermediates:  # Retain encoded data and candidates for inspection.
            return _run(data_path, hierarchy_path, minwus, max_rows, output, monitor, report_root,
                        presence_only_binary)
        with tempfile.TemporaryDirectory(prefix='explanation_run_') as work:
            return _run(data_path, hierarchy_path, minwus, max_rows, Path(work), monitor, report_root,
                        presence_only_binary)


def _run(data_path, hierarchy_path, minwus, max_rows, output, monitor, report_root,
         presence_only_binary):
    if not 0 < minwus <= 1:
        raise ValueError('minwus must be greater than 0 and at most 1')
    if max_rows < 1:
        raise ValueError('rows must be at least 1 and includes the baseline row')
    started = time.perf_counter()
    prepared = prepare(Path(data_path), Path(hierarchy_path),
                       presence_only_binary=presence_only_binary)
    compilation_started = time.perf_counter()
    binary, env = compile_engine()
    compilation_seconds = time.perf_counter() - compilation_started
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    # Prevent an existing validation file from being mistaken for this run's output.
    (output/'verification.json').unlink(missing_ok=True)
    internal = output/'internal'
    internal.mkdir(exist_ok=True)
    count = len(prepared['names']) - 1
    fields = [f'f{i}' for i in range(count)]
    encoded_path = internal/'data.csv'
    write_csv(encoded_path, fields + ['outcome'], [row + [outcome] for row, outcome in zip(prepared['encoded'], prepared['outcomes'])], line_ending='\n')
    mappings = prepared['mappings']
    reverse = [{str(code): value for value, code in mapping.items()} for mapping in mappings]
    def readable(index, value):
        if value == '*':
            return '*'
        if ',' in value:
            label = prepared['interval_labels'][index].get(value)
            if label is None:
                raise ValueError(f'Unknown internal hierarchy interval {value!r}')
            return label
        return reverse[index][value]
    print(f'Mining {len(prepared["rows"])} rows, {count} features...', flush=True)
    db = prepared['db']
    mining_started = time.perf_counter()
    hit = mine_fwuis(db, db[3], minwus)
    mining_seconds = time.perf_counter() - mining_started
    candidate_rows, readable_rows = [], []
    for level in sorted(hit.levels, key=lambda level: level.itemset_size):
        for group in level.equivalence_classes:
            for node in group.nodes:
                cells, displayed = ['*'] * count, ['*'] * count
                for item in sorted(node.itemset):
                    feature = item.split('::', 1)[0] if '::' in item else item.split('=', 1)[0]
                    index = int(feature[1:])
                    values = prepared['node_labels'].get(item, item.split('=', 1)[1])
                    if cells[index] != '*':
                        raise ValueError('Miner returned multiple conditions for one feature')
                    cells[index] = f'{feature}={values}'
                    ids = values.split('|')
                    displayed[index] = prepared['node_names'].get(item, readable(index, f'{ids[0]},{ids[-1]}' if len(ids) > 1 else ids[0]))
                stats = [len(node.itemset), f'{node.wus:.12g}', len(node.tidset)]
                candidate_rows.append(stats + cells)
                readable_rows.append(stats + displayed)
    candidate_path = internal/'patterns.csv'
    write_csv(candidate_path, ['level', 'wus', 'support_count'] + fields, candidate_rows, line_ending='\n')
    # Row IDs refer to the same prepared-row order written to internal/data.csv.
    # Keep the compact transport separate from the readable candidate CSV.
    tidsets_path = internal/'tidsets.bin'
    with tidsets_path.open('wb') as handle:
        handle.write(b'TIDS1')
        handle.write(struct.pack('<II', len(prepared['rows']), len(candidate_rows)))
        for level in sorted(hit.levels, key=lambda level: level.itemset_size):
            for group in level.equivalence_classes:
                for node in group.nodes:
                    tids = sorted(node.tidset)
                    handle.write(struct.pack('<I', len(tids)))
                    handle.write(struct.pack(f'<{len(tids)}I', *tids))
    def metric_name(preferred, existing):
        while preferred in existing:
            preferred = 'pattern_' + preferred
        return preferred
    metric_columns = [metric_name(name, prepared['names']) for name in ['level', 'wus', 'support_count']]
    write_csv(output/'patterns.csv', metric_columns + prepared['names'][:-1], readable_rows)
    (internal/'mappings.json').write_text(json.dumps({'features': prepared['names'][:-1], 'codes': mappings, 'interval_labels': prepared['interval_labels']}, ensure_ascii=False, indent=2), encoding='utf-8')
    raw_table = internal/'explanations.csv'
    telemetry_path = internal/'metrics.json'
    explanation_started = time.perf_counter()
    with subprocess.Popen([str(binary), str(encoded_path.resolve()), str(candidate_path.resolve()), str(count), str(max_rows), str(raw_table.resolve()), str(tidsets_path.resolve()), str(telemetry_path.resolve())], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env) as process:
        monitor.child = process
        monitor.sample()
        stdout, stderr = process.communicate()
        monitor.sample()
        monitor.child = None
        result = subprocess.CompletedProcess(process.args, process.returncode, stdout, stderr)
    explanation_seconds = time.perf_counter() - explanation_started
    (output/'explanations.log').write_text(result.stdout + result.stderr, encoding='utf-8')
    if result.returncode:
        raise ValueError(f'Explanation engine failed: {result.stderr}')
    with raw_table.open(newline='', encoding='utf-8') as handle:
        raw_rows = list(csv.reader(handle, delimiter=';'))[1:]
    if not 1 <= len(raw_rows) <= max_rows:
        raise ValueError('Engine table must contain a baseline and stay within the requested total size')
    labeled = [[readable(i, row[i]) for i in range(count)] + row[count:] for row in raw_rows]
    table = output/'explanations.csv'
    support_column = metric_name('support', prepared['names'])
    write_csv(table, prepared['names'] + [support_column], labeled, ';')
    render_table(read_table(table, 'utf-8-sig'), output/'explanations.png', 16, 10, True, 1.0)
    manifest = {'data': str(Path(data_path).resolve()), 'hierarchies': str(Path(hierarchy_path).resolve()),
                'data_sha256': hashlib.sha256(Path(data_path).read_bytes()).hexdigest(),
                'hierarchies_sha256': hashlib.sha256(Path(hierarchy_path).read_bytes()).hexdigest(),
                'minwus': minwus, 'maximum_rows': max_rows,
                'maximum_patterns': max_rows - 1, 'k_includes_baseline': True,
                'row_count_semantics': 'total_table_rows_including_baseline', 'penalties': False,
                'presence_only_binary': presence_only_binary,
                'mined_patterns': len(candidate_rows), 'selected_patterns': len(labeled)-1,
                'pattern_metric_columns': metric_columns, 'explanation_support_column': support_column,
                'mining_seconds': mining_seconds, 'explanation_seconds': explanation_seconds,
                'tidset_reuse': True, 'support_count_reuse': True,
                'elapsed_seconds': time.perf_counter()-started,
                '_mining_counters': hit.metrics}
    monitor.stop()
    telemetry = json.loads(telemetry_path.read_text(encoding='utf-8'))
    save_report(output, manifest, prepared, hit.nodes, telemetry, monitor, compilation_seconds, report_root)
    (output/'run.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('dataset', type=Path, help='Path to the input CSV; last column is the numeric outcome')
    parser.add_argument('--hierarchy', required=True, type=Path,
                        help='Path to a hierarchy CSV; use a header-only file for flat mode')
    parser.add_argument('--minwus', required=True, type=float, help='Minimum WUS threshold, in (0, 1]')
    parser.add_argument('--rows', required=True, type=int,
                        help='Maximum total table rows including the baseline (at least 1)')
    parser.add_argument('--presence-only-binary', action='store_true',
                        help='Treat 0 in binary feature columns as absence rather than a mineable item')
    parser.add_argument('--output-dir', type=Path, default=DIRECTORY.parent/'outputs',
                        help='Output root containing numbered reports/ (default: submission outputs/)')
    args = parser.parse_args()
    if args.dataset.suffix.lower() != '.csv':
        parser.error('dataset must be a CSV file')
    try:
        run(args.dataset, args.hierarchy, args.minwus, args.rows,
            args.output_dir/'work', presence_only_binary=args.presence_only_binary)
    except (ValueError, OSError) as error:
        parser.exit(1, f'Error: {error}\n')


if __name__ == '__main__':
    main()
