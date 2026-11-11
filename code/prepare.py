"""Validate readable inputs and construct hierarchy-contiguous internal IDs."""
from __future__ import annotations

import csv
import json
from decimal import Decimal, InvalidOperation
from pathlib import Path


def read_csv(path: Path, delimiter=','):
    with path.open(encoding='utf-8-sig', newline='') as handle:
        rows = list(csv.reader(handle, delimiter=delimiter))
    if not rows or not rows[0] or any(not name for name in rows[0]):
        raise ValueError(f'{path}: missing column names')
    if len(set(rows[0])) != len(rows[0]):
        raise ValueError(f'{path}: duplicate column names')
    if any(len(row) != len(rows[0]) for row in rows[1:]):
        raise ValueError(f'{path}: inconsistent number of columns')
    return rows[0], rows[1:]


def number(value):
    try:
        result = Decimal(str(value))
    except InvalidOperation as error:
        raise ValueError(f'Expected a number, got {value!r}') from error
    if not result.is_finite():
        raise ValueError(f'Expected a finite number, got {value!r}')
    return result


def ordered(values):
    try:
        return sorted(values, key=lambda value: (number(value), value))
    except ValueError:
        return sorted(values)


def prepare(data_path: Path, hierarchy_path: Path, *, presence_only_binary: bool = False):
    names, rows = read_csv(data_path)
    if len(names) < 2 or not rows:
        raise ValueError('Data must contain at least one feature, a final outcome column, and one row')
    if any(value == '' for row in rows for value in row):
        raise ValueError('Missing values are not supported; supply an explicit category or a numeric outcome')
    outcomes = [number(row[-1]) for row in rows]
    if any(value < 0 for value in outcomes):
        raise ValueError('Outcome must be nonnegative for the existing normalized-measure explanation algorithm')
    # A positive Decimal may overflow/underflow the double precision engine.
    import math
    if any(not math.isfinite(float(value)) or (value != 0 and float(value) == 0) for value in outcomes):
        raise ValueError('Outcome is outside the supported floating-point range')
    if not math.isfinite(sum(map(float, outcomes))):
        raise ValueError('Outcome total is outside the supported floating-point range')
    hnames, hrows = read_csv(hierarchy_path)
    required = {'feature', 'node', 'parent', 'members', 'lower', 'upper', 'closed'}
    if set(hnames) != required:
        raise ValueError(f'Hierarchy columns must be: {", ".join(sorted(required))}')
    definitions = {name: [] for name in names[:-1]}
    for row in hrows:
        entry = dict(zip(hnames, row))
        if entry['feature'] not in definitions:
            raise ValueError(f'Unknown hierarchy feature {entry["feature"]!r}; the outcome cannot have a hierarchy')
        definitions[entry['feature']].append(entry)

    mappings, trees, node_labels, interval_labels, node_names = [], [], {}, [], {}
    for index, feature in enumerate(names[:-1]):
        internal = f'f{index}'
        values = set(row[index] for row in rows)
        specs = definitions[feature]
        nodes = {}
        for spec in specs:
            name = spec['node']
            if not name or name in nodes:
                raise ValueError(f'{feature}: hierarchy node names must be nonempty and unique')
            nodes[name] = {**spec, 'children': [], 'id': f'{internal}::node={len(nodes)}'}
        roots = []
        for name, node in nodes.items():
            parent = node['parent']
            if parent:
                if parent not in nodes:
                    raise ValueError(f'{feature}/{name}: unknown parent {parent!r}')
                nodes[parent]['children'].append(name)
            else:
                roots.append(name)
        visiting, done = set(), set()

        def resolve(name):
            if name in visiting:
                raise ValueError(f'{feature}: hierarchy cycle at {name!r}')
            node = nodes[name]
            if name in done:
                return node['values']
            visiting.add(name)
            child_sets = [resolve(child) for child in node['children']]
            union = set()
            for child_set in child_sets:
                if union & child_set:
                    raise ValueError(f'{feature}/{name}: overlapping sibling nodes')
                union |= child_set
            members = node['members']
            has_range = bool(node['closed'] or node['lower'] or node['upper'])
            if members and has_range:
                raise ValueError(f'{feature}/{name}: use members OR a numeric range')
            if members:
                try:
                    listed = json.loads(members)
                except json.JSONDecodeError as error:
                    raise ValueError(f'{feature}/{name}: members must be a JSON array') from error
                if not isinstance(listed, list) or not listed or any(not isinstance(v, (str, int, float)) or isinstance(v, bool) for v in listed):
                    raise ValueError(f'{feature}/{name}: members must be a nonempty array of strings or numbers')
                listed = [str(value) for value in listed]
                if len(set(listed)) != len(listed):
                    raise ValueError(f'{feature}/{name}: duplicate members')
                node['member_order'] = listed
                matched = values & set(listed)
            elif has_range:
                closed = node['closed'] or '[)'
                if closed not in ('[]', '[)', '(]', '()'):
                    raise ValueError(f'{feature}/{name}: closed must be [], [), (], or ()')
                low = number(node['lower']) if node['lower'] else None
                high = number(node['upper']) if node['upper'] else None
                if low is not None and high is not None and (low > high or (low == high and closed != '[]')):
                    raise ValueError(f'{feature}/{name}: empty or reversed range')
                matched = set()
                for value in values:
                    numeric = number(value)
                    if low is not None and (numeric < low or (numeric == low and closed[0] == '(')):
                        continue
                    if high is not None and (numeric > high or (numeric == high and closed[1] == ')')):
                        continue
                    matched.add(value)
            elif node['children']:
                matched = union
            else:
                raise ValueError(f'{feature}/{name}: leaf hierarchy node needs members or a range')
            if not union <= matched:
                raise ValueError(f'{feature}/{name}: children extend outside their parent')
            node['values'] = matched
            visiting.remove(name)
            done.add(name)
            return matched

        for name in nodes:
            resolve(name)
        covered = set()
        for name in roots:
            if covered & nodes[name]['values']:
                raise ValueError(f'{feature}: overlapping root nodes')
            covered |= nodes[name]['values']
        if nodes and covered != values:
            raise ValueError(f'{feature}: hierarchy does not cover observed values {ordered(values - covered)[:5]}')

        leaf_order, leaf_parent, depths = [], {}, {}
        def walk(name, depth):
            node = nodes[name]
            depths[name] = depth
            child_values = set()
            for child in node['children']:
                walk(child, depth + 1)
                child_values |= nodes[child]['values']
            direct = node['values'] - child_values
            order = node.get('member_order', ordered(direct))
            for value in order:
                if value in direct:
                    leaf_order.append(value)
                    leaf_parent[value] = name
        for name in roots:
            walk(name, 0)
        if not nodes:
            leaf_order = ordered(values)
        mapping = {value: code for code, value in enumerate(leaf_order)}
        mappings.append(mapping)
        intervals = {}
        # Visiting deeper nodes first lets a more general node supply the label
        # when several hierarchy nodes cover the same observed values.
        for name in sorted(nodes, key=lambda n: depths[n], reverse=True):
            ids = sorted(mapping[value] for value in nodes[name]['values'])
            if ids:
                intervals[f'{ids[0]},{ids[-1]}'] = name
                node_labels[nodes[name]['id']] = '|'.join(map(str, ids))
                node_names[nodes[name]['id']] = name
        interval_labels.append(intervals)
        if nodes:
            parents = {}
            children = {}
            def attach(child, parent):
                parents[child] = parent
                children.setdefault(parent, []).append(child)
            for value, parent in leaf_parent.items():
                attach(f'{internal}={mapping[value]}', nodes[parent]['id'])
            for name, node in nodes.items():
                if node['values']:
                    attach(node['id'], nodes[node['parent']]['id'] if node['parent'] else internal)
            def nested(item):
                return {'id': item, 'children': [nested(child) for child in children.get(item, [])]}
            trees.append({'name': internal, 'root_is_item': False, 'root': nested(internal), 'parent_map': parents})
    encoded = [[mappings[i][value] for i, value in enumerate(row[:-1])] for row in rows]
    # Presence-only encoding omits binary zeros from mining transactions.
    # The complete encoded rows remain available for outcome reconstruction,
    # including rows that produce empty mining transactions.
    binary_feature_indexes = {
        index for index in range(len(names) - 1)
        if {row[index] for row in rows} <= {'0', '1'}
    } if presence_only_binary else set()
    transactions = [
        {
            f'f{i}={value}': 1.0
            for i, value in enumerate(row)
            if i not in binary_feature_indexes or value != mappings[i]['0']
        }
        for row in encoded
    ]
    items = sorted({item for transaction in transactions for item in transaction})
    return {'names': names, 'rows': rows, 'encoded': encoded, 'outcomes': list(map(str, outcomes)),
            'mappings': mappings, 'interval_labels': interval_labels, 'node_labels': node_labels,
            'node_names': node_names,
            'db': (transactions, items, [1.0] * len(items), trees)}
