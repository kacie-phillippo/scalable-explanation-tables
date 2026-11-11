"""MINE_FWUIS algorithm and HIT-tree data structures.

Hierarchy-aware frequent weighted utility itemset mining without penalties.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, FrozenSet, List, Mapping, Sequence, Tuple, Union



Transaction = Union[List[str], Dict[str, float]]
HierarchyTree = Dict[str, Any]
ITEM_QUANTITY = 1.0
HQDB = Tuple[
    List[Transaction],  # T
    List[str],          # I
    List[float],        # W
    List[HierarchyTree],  # Tr stored in DB
]


@dataclass(frozen=True)
class HITNode:
    """One HIT-tree node: <X, tidset(X), wus(X)>."""

    itemset: FrozenSet[str]
    tidset: FrozenSet[int]
    wus: float


@dataclass
class EquivalenceClass:
    """Nodes sharing the prefix represented by ``parent_itemset``."""

    parent_itemset: FrozenSet[str]
    nodes: List[HITNode] = field(default_factory=list)


@dataclass
class HITLevel:
    """One itemset-size level in a HIT-tree."""

    itemset_size: int
    equivalence_classes: List[EquivalenceClass] = field(default_factory=list)


@dataclass
class WeightTable:
    """Weights of raw database items and hierarchy-derived parent items."""

    weights: Dict[str, float] = field(default_factory=dict)

    def get(self, item: str) -> float:
        """Return an item's weight, defaulting uninitialized parents to zero."""
        return self.weights.get(item, 0.0)

    def insert(self, item: str, weight: float) -> None:
        """Insert or replace one item weight."""
        self.weights[item] = float(weight)


@dataclass
class HITTree:
    """The output tree containing the discovered FWUIs."""

    minwus: float
    levels: List[HITLevel] = field(default_factory=list)
    weight_table: WeightTable = field(default_factory=WeightTable)
    hierarchy_weights: Dict[str, Dict[str, float]] = field(default_factory=dict)
    twu: Dict[int, float] = field(default_factory=dict)
    sum_twu: float = 0.0
    metrics: Dict[str, int] = field(default_factory=dict)

    @property
    def nodes(self) -> List[HITNode]:
        """Return every node in the tree in level/class order."""
        return [
            node
            for level in self.levels
            for equivalence_class in level.equivalence_classes
            for node in equivalence_class.nodes
        ]


def weight_func(weight_a: float, weight_b: float) -> float:
    """Combine child and parent weights using their maximum."""
    return max(weight_a, weight_b)


def quantity_func(quantity_a: float, quantity_b: float) -> float:
    """Combine quantities for a shared parent using their maximum."""
    return max(quantity_a, quantity_b)


def calculate_weights(
    R: HierarchyTree,
    weight_table: WeightTable,
) -> Dict[str, float]:
    """Run the recursive CALCULATE_WEIGHTS procedure on hierarchy ``R``.

    Starting at the named root, recursively visit the children before inserting
    each parent.  A parent's weight is the result of applying ``weight_func`` to
    its own current weight and the calculated weights of its children.

    Returns
    -------
    dict
        ``W_A = {h: weight(h)}`` for parent items ``h`` in this hierarchy.
    """
    # The initial argument is a complete compiled tree.  Recursive arguments
    # are nested nodes from that tree.
    is_complete_tree = "root" in R
    node = R["root"] if is_complete_tree else R

    if "id" not in node:
        raise ValueError("Hierarchy node R must contain an 'id'")

    hierarchy_items: set[str] = set()
    if is_complete_tree:
        raw_prefix = f"{R['name']}="
        hierarchy_items = {
            str(item)
            for edge in R.get("edges", [])
            for item in (edge["child"], edge["parent"])
            if not str(item).startswith(raw_prefix)
        }

        # Parent items start with weight zero.
        for hierarchy_item in hierarchy_items:
            if hierarchy_item not in weight_table.weights:
                weight_table.insert(hierarchy_item, 0.0)

    parent_item = str(node["id"])
    parent_weight = weight_table.get(parent_item)
    children = node.get("children", [])

    # A node without children is a leaf, which stops the recursion.
    for r in node.get("children", []):
        child_item = str(r["id"])
        child_weight = weight_table.get(child_item)

        if child_weight == 0:
            calculate_weights(r, weight_table)
            child_weight = weight_table.get(child_item)

        parent_weight = weight_func(parent_weight, child_weight)

    # Only hierarchy nodes are parent items.  Raw leaves already have weights
    # supplied by the input database.
    if children or parent_item in hierarchy_items:
        weight_table.insert(parent_item, parent_weight)

    if not is_complete_tree:
        return {}

    return {
        hierarchy_item: weight_table.get(hierarchy_item)
        for hierarchy_item in hierarchy_items
    }


def add_parent(
    DB: HQDB,
    Tr: Sequence[HierarchyTree],
    weight_table: WeightTable | None = None,
    metrics=None,
) -> HQDB:
    """Return a copy of ``DB`` with hierarchy parents in its transactions.

    For every original item, all direct and transitive parents found in ``Tr``
    are inserted.  If multiple children produce the same parent, its quantity
    is combined with ``quantity_func``.  Explicit input quantities are
    preserved; list transactions are treated as unit-quantity items.

    The returned ``I`` and ``W`` are extended with the inserted parent items so
    that every transaction item remains represented in the item universe.
    ``DB`` itself is not modified.
    """
    T, I, W, _db_trees = DB

    if weight_table is None:
        weight_table = WeightTable(
            {item: float(weight) for item, weight in zip(I, W)}
        )

    parent_map: Dict[str, str] = {}
    for R in Tr:
        structural_root = (
            None
            if R.get("root_is_item", True)
            else str(R["root"]["id"])
        )
        for child, parent in R.get("parent_map", {}).items():
            child = str(child)
            parent = str(parent)
            # A structural root names the hierarchy/column but is not an item
            # and must not be inserted into a transaction.
            if parent == structural_root:
                continue
            existing_parent = parent_map.get(child)
            if existing_parent is not None and existing_parent != parent:
                raise ValueError(
                    f"Hierarchy item {child!r} has conflicting parents: "
                    f"{existing_parent!r} and {parent!r}"
                )
            parent_map[child] = parent

    augmented_transactions: List[Transaction] = []
    inserted_parents: set[str] = set()

    if metrics is not None:
        metrics['parent_augmentation_scans'] = metrics.get('parent_augmentation_scans', 0) + 1
    for tid, transaction in enumerate(T):
        # The dictionary represents item -> quantity and preserves insertion
        # order. It supports explicit quantities and unit-quantity lists.
        augmented = dict(_transaction_items(transaction))

        for item, _quantity in _transaction_items(transaction):
            child = item
            visited = {child}

            while child in parent_map:
                parent = parent_map[child]
                if parent in visited:
                    raise ValueError(
                        f"Hierarchy cycle detected from {item!r} "
                        f"in transaction {tid}"
                    )
                visited.add(parent)

                child_quantity = augmented.get(child, ITEM_QUANTITY)
                if parent in augmented:
                    augmented[parent] = quantity_func(
                        augmented[parent], child_quantity
                    )
                else:
                    augmented[parent] = child_quantity
                    inserted_parents.add(parent)

                child = parent

        if isinstance(transaction, Mapping):
            augmented_transactions.append(augmented)
        else:
            augmented_transactions.append(list(augmented))

    item_weights = {
        item: float(weight) for item, weight in zip(I, W)
    }
    for parent in inserted_parents:
        item_weights[parent] = weight_table.get(parent)

    augmented_items = sorted(item_weights)
    augmented_weights = [item_weights[item] for item in augmented_items]
    return (
        augmented_transactions,
        augmented_items,
        augmented_weights,
        list(Tr),
    )


def _transaction_items(
    transaction: Transaction,
) -> List[Tuple[str, float]]:
    """Return ``(item, quantity)`` pairs without parsing quantities from IDs."""
    if isinstance(transaction, Mapping):
        return [
            (str(item), float(quantity))
            for item, quantity in transaction.items()
            if float(quantity) != 0
        ]
    return [(str(item), ITEM_QUANTITY) for item in transaction]


def calculate_twu(
    DB: HQDB,
    weight_table: WeightTable,
    metrics=None,
) -> Tuple[Dict[int, float], float]:
    """Calculate a TWU value for every transaction in ``DB``.

    Transactions are identified by their zero-based transaction ID because the
    transaction lists themselves cannot be dictionary keys.  For each
    transaction, quantities affect the weighted-utility numerator, while the
    denominator is the number of distinct items in the augmented transaction:

    ``twu[t] = sum(quantity(i) * weight(i)) / len(t)``

    ``ADD_PARENT`` is invoked first to create the parent-augmented database.
    """
    _T, _I, _W, Tr = DB
    parent_augmented_DB = add_parent(DB, Tr, weight_table, metrics)
    T, _I, _W, _Tr = parent_augmented_DB

    if metrics is not None:
        metrics['transaction_utility_scans'] = metrics.get('transaction_utility_scans', 0) + 1
    twu: Dict[int, float] = {}
    sum_twu = 0.0

    for tid, transaction in enumerate(T):
        weighted_utility = 0.0
        s = 0.0

        for item, quantity in _transaction_items(transaction):
            weighted_utility += quantity * weight_table.get(item)
            s += 1.0

        # Empty transactions have zero utility. With unit weights and quantities,
        # every nonempty transaction has utility one, so the WUS denominator
        # counts nonempty transactions rather than all input rows.
        twu[tid] = 0.0 if s == 0 else weighted_utility / s

    if metrics is not None:
        metrics['transaction_utility_total_scans'] = metrics.get('transaction_utility_total_scans', 0) + 1
    sum_twu = sum(twu.values())
    return twu, sum_twu


def calculate_wus(
    tidset: FrozenSet[int],
    twu: Mapping[int, float],
    sum_twu: float,
    metrics=None,
) -> float:
    """Calculate ``wus(X)`` from ``tidset(X)``.

    ``wus(X) = sum(twu[tid] for tid in tidset(X)) / sum_twu``
    """
    if metrics is not None:
        metrics['support_calculations'] = metrics.get('support_calculations', 0) + 1
    if sum_twu == 0:
        raise ValueError("Cannot calculate WUS when sum_twu is zero")

    S = 0.0
    for tid in tidset:
        if tid not in twu:
            raise ValueError(f"Transaction ID {tid} has no TWU value")
        S += twu[tid]

    return S / sum_twu


def _find_one_fwuis(
    DB: HQDB,
    Tr: Sequence[HierarchyTree],
    weight_table: WeightTable,
    twu: Mapping[int, float],
    sum_twu: float,
    minwus: float,
    metrics=None,
) -> List[HITNode]:
    """Find ``F = {i in (I union J) | wus(i) >= minwus}``."""
    parent_augmented_DB = add_parent(DB, Tr, weight_table, metrics)
    T, I_union_J, _W, _Tr = parent_augmented_DB

    if metrics is not None:
        metrics['singleton_index_scans'] = metrics.get('singleton_index_scans', 0) + 1
    tidsets: Dict[str, set[int]] = {
        item: set() for item in I_union_J
    }
    for tid, transaction in enumerate(T):
        for item in transaction:
            tidsets[item].add(tid)

    F: List[HITNode] = []
    for item in I_union_J:
        tidset = frozenset(tidsets[item])
        wus = calculate_wus(tidset, twu, sum_twu, metrics)
        if wus >= minwus:
            F.append(
                HITNode(
                    itemset=frozenset({item}),
                    tidset=tidset,
                    wus=wus,
                )
            )

    return F


def _ancestor_map(
    Tr: Sequence[HierarchyTree],
) -> Dict[str, FrozenSet[str]]:
    """Return all direct and transitive ancestors of each hierarchy item."""
    direct_parent: Dict[str, str] = {}
    for R in Tr:
        for child, parent in R.get("parent_map", {}).items():
            child = str(child)
            parent = str(parent)
            existing = direct_parent.get(child)
            if existing is not None and existing != parent:
                raise ValueError(
                    f"Hierarchy item {child!r} has conflicting parents"
                )
            direct_parent[child] = parent

    ancestors: Dict[str, FrozenSet[str]] = {}
    for item in direct_parent:
        item_ancestors: set[str] = set()
        current = item
        while current in direct_parent:
            parent = direct_parent[current]
            if parent in item_ancestors:
                raise ValueError(
                    f"Hierarchy cycle detected from item {item!r}"
                )
            item_ancestors.add(parent)
            current = parent
        ancestors[item] = frozenset(item_ancestors)

    return ancestors


def _contains_parent_child_pair(
    itemset: FrozenSet[str],
    ancestors: Mapping[str, FrozenSet[str]],
) -> bool:
    """Return whether two items in ``itemset`` are hierarchy-related."""
    for item in itemset:
        if ancestors.get(item, frozenset()) & itemset:
            return True
    return False


def _insert_equivalence_class(
    hit_tree: HITTree,
    itemset_size: int,
    parent_itemset: FrozenSet[str],
    nodes: List[HITNode],
) -> None:
    """Insert one nonempty equivalence class into its HIT-tree level."""
    if not nodes:
        return

    level = next(
        (
            existing
            for existing in hit_tree.levels
            if existing.itemset_size == itemset_size
        ),
        None,
    )
    if level is None:
        level = HITLevel(itemset_size=itemset_size)
        hit_tree.levels.append(level)

    level.equivalence_classes.append(
        EquivalenceClass(
            parent_itemset=parent_itemset,
            nodes=nodes,
        )
    )


def CREATE_HIT_tree(
    F: Sequence[HITNode],
    hit_tree: HITTree,
    ancestors: Mapping[str, FrozenSet[str]],
) -> None:
    """Recursively extend an equivalence class of FWUI nodes.

    Each node ``F_i`` is combined with later sibling nodes ``F_j``.  A
    candidate is rejected if it contains an ancestor together with one of its
    descendants.  Otherwise its tidset is intersected, its WUS is calculated,
    and it is retained when ``wus(X) >= minwus``.
    """
    for i, node_i in enumerate(F):
        P: List[HITNode] = []

        for node_j in F[i + 1:]:
            hit_tree.metrics['itemset_join_attempts'] = hit_tree.metrics.get('itemset_join_attempts', 0) + 1
            X = node_i.itemset | node_j.itemset

            # Sibling joins must add exactly one new item.  This also prevents
            # malformed joins and duplicate candidates at deeper levels.
            expected_size = len(node_i.itemset) + 1
            if len(X) != expected_size:
                continue

            if _contains_parent_child_pair(X, ancestors):
                continue

            hit_tree.metrics['tidset_intersections'] = hit_tree.metrics.get('tidset_intersections', 0) + 1
            T = node_i.tidset & node_j.tidset
            wus_X = calculate_wus(
                T,
                hit_tree.twu,
                hit_tree.sum_twu,
                hit_tree.metrics,
            )

            if wus_X >= hit_tree.minwus:
                P.append(
                    HITNode(
                        itemset=frozenset(X),
                        tidset=frozenset(T),
                        wus=wus_X,
                    )
                )

        if P:
            _insert_equivalence_class(
                hit_tree=hit_tree,
                itemset_size=len(P[0].itemset),
                parent_itemset=node_i.itemset,
                nodes=P,
            )
            CREATE_HIT_tree(P, hit_tree, ancestors)


def _validate_inputs(
    DB: HQDB,
    Tr: Sequence[HierarchyTree],
    minwus: float,
) -> None:
    """Check the structural assumptions needed by MINE_FWUIS."""
    if not isinstance(DB, tuple) or len(DB) != 4:
        raise TypeError("DB must be the HQDB tuple <T, I, W, Tr>")

    T, I, W, _db_trees = DB

    if len(I) != len(W):
        raise ValueError("I and W must have the same length")
    if not isinstance(minwus, (int, float)):
        raise TypeError("minwus must be numeric")
    if not isinstance(Tr, Sequence):
        raise TypeError("Tr must be a sequence of hierarchy trees")

    known_items = set(I)
    for tid, transaction in enumerate(T):
        unknown = set(transaction) - known_items
        if unknown:
            raise ValueError(
                f"Transaction {tid} contains items absent from I: "
                f"{sorted(unknown)}"
            )


def mine_fwuis(DB: HQDB, Tr: Sequence[HierarchyTree], minwus: float) -> HITTree:
    """Mine FWUIs from an HQDB and return them in a HIT-tree.

    Parameters
    ----------
    DB:
        Quantitative database tuple ``<T, I, W, Tr>``.
    Tr:
        The hierarchy trees used during mining.  This is accepted separately
        because it is an explicit input to MINE_FWUIS, even though the current
        HQDB tuple also stores the trees.
    minwus:
        Minimum WUS threshold for each returned itemset.
    """
    _validate_inputs(DB, Tr, minwus)
    _T, I, W, _db_trees = DB
    weight_table = WeightTable(
        {item: float(weight) for item, weight in zip(I, W)}
    )
    hit_tree = HITTree(
        minwus=float(minwus),
        weight_table=weight_table,
    )

    # Calculate and store parent weights for every hierarchy tree first.
    for R in Tr:
        W_A = calculate_weights(R, weight_table)
        hit_tree.hierarchy_weights[R["name"]] = W_A

    # ADD_PARENT uses all trees, so TWU is calculated after the complete
    # WeightTable has been constructed.
    hit_tree.metrics['input_validation_scans'] = 1
    hit_tree.twu, hit_tree.sum_twu = calculate_twu(DB, weight_table, hit_tree.metrics)

    # Find F, the set of all 1-FWUIs, and store it as level 1 of the HIT-tree.
    F = _find_one_fwuis(
        DB,
        Tr,
        weight_table,
        hit_tree.twu,
        hit_tree.sum_twu,
        hit_tree.minwus,
        hit_tree.metrics,
    )
    if F:
        hit_tree.levels.append(
            HITLevel(
                itemset_size=1,
                equivalence_classes=[
                    EquivalenceClass(
                        parent_itemset=frozenset(),
                        nodes=F,
                    )
                ],
            )
        )
        CREATE_HIT_tree(F, hit_tree, _ancestor_map(Tr))
        hit_tree.levels.sort(key=lambda level: level.itemset_size)

    return hit_tree

