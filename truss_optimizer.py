"""
Structural Optimization of 3D Trusses using pymoo

Minimizes total mass subject to Eurocode 3 (EN 1993-1-1) constraints:
  - Tension resistance (§6.2.3)
  - Compression resistance (§6.2.4)
  - Flexural buckling (§6.3.1)
  - Slenderness limits (L/i <= 200 for compression members)
  - Serviceability deflection (§7.2, L/250)

Supports discrete (CHS catalog) and continuous (area variable) optimization.
"""

import math
import csv
import os
import numpy as np

from pymoo.core.problem import ElementwiseProblem
from pymoo.algorithms.soo.nonconvex.pso import PSO
from pymoo.algorithms.soo.nonconvex.ga import GA
from pymoo.algorithms.soo.nonconvex.de import DE
from pymoo.optimize import minimize
from pymoo.termination import get_termination

from truss3D_analysis_from_file import read_truss_file, run_analysis_in_memory, find_joint_by_coords


# ---------------------------------------------------------------------------
#  Steel material constants (S355)
# ---------------------------------------------------------------------------
FY = 355.0        # Yield strength (MPa)
E = 200000.0      # Young's modulus (MPa)
GAMMA_M0 = 1.0    # Partial safety factor for cross-section resistance
GAMMA_M1 = 1.0    # Partial safety factor for buckling resistance
RHO_STEEL = 7500  # Volumic mass of steel (kg/m³)


# ---------------------------------------------------------------------------
#  Section catalog loader
# ---------------------------------------------------------------------------

def load_section_catalog(csv_path):
    """
    Load CHS sections with full properties from CSV.
    Only includes rows with complete data (d, t, A, I, i).
    Returns list sorted by area ascending.

    Each entry: {'ID': str, 'd': mm, 't': mm, 'A': mm2, 'I': mm4, 'i': mm}
    """
    sections = []
    with open(csv_path, 'r') as f:
        reader = csv.DictReader(f)
        for row in reader:
            sid = row['ID']
            if sid == '[]':
                continue
            try:
                d = row['d'].strip()
                t = row['t'].strip()
                a = row['A'].strip()
                I_val = row['I'].strip()
                i_val = row['i'].strip()
                if not all([d, t, a, I_val, i_val]):
                    continue
                sections.append({
                    'ID': sid,
                    'd': float(d),
                    't': float(t),
                    'A': float(a) * 100,       # cm2 -> mm2
                    'I': float(I_val) * 1e4,   # cm4 -> mm4
                    'i': float(i_val) * 10,     # cm -> mm
                })
            except (ValueError, KeyError):
                continue

    sections.sort(key=lambda s: s['A'])
    return sections


# ---------------------------------------------------------------------------
#  Area lookup from eurocode CSV (all rows, only needs ID + A)
# ---------------------------------------------------------------------------

def load_area_lookup(csv_path):
    """
    Load {section_id: A_mm2} from a CSV that has at minimum ID and A columns.
    Skips rows where ID is '[]' or A is empty.
    Area column is in cm² — converted to mm² (× 100).

    Works with both CHS sections (full data) and custom sections (area only),
    as found in CHS_eurocode.csv.
    """
    lookup = {}
    with open(csv_path, 'r') as f:
        reader = csv.DictReader(f)
        for row in reader:
            sid = row.get('ID', '').strip()
            if not sid or sid == '[]':
                continue
            a_str = row.get('A', '').strip()
            if not a_str:
                continue
            try:
                lookup[sid] = float(a_str) * 100.0   # cm² → mm²
            except ValueError:
                continue
    return lookup


# ---------------------------------------------------------------------------
#  Section group mapper
# ---------------------------------------------------------------------------

def _natural_sort_key(name):
    """Natural sort key: 'A1' < 'A2' < 'A10' (not lexicographic 'A1' < 'A10' < 'A2')."""
    import re
    parts = re.split(r'(\d+)', name)
    return [int(p) if p.isdigit() else p.lower() for p in parts]


def get_section_groups(truss_data, area_lookup=None):
    """
    Identify section groups from truss element data.

    When area_lookup ({section_id: A_mm2}) is provided (e.g. loaded from
    CHS_eurocode.csv via load_area_lookup), elements are grouped by their
    initial cross-section area rounded to the nearest mm².  Elements with
    identical area share one group and receive the same optimized catalog
    section, regardless of their section name.

    Falls back to numeric-suffix extraction ("A4_1.735" → group "173.5 mm²")
    or full section name if the section is not in area_lookup.

    Returns:
        groups: dict {group_key: [elem_indices_in_element_data]}
            group_key is "<area_mm2_rounded>" (e.g. "903") when area-based.
        group_order: list of group keys (natural numeric sort)
    """
    groups = {}
    for idx, (elem_id, start, end, section_name) in enumerate(truss_data['elements']):
        if area_lookup and section_name in area_lookup:
            # Known section: group by its area in mm² (rounded to int)
            group = f"{round(area_lookup[section_name])}"
        else:
            # Fallback: parse numeric suffix as area (cm²) → convert to mm²
            parts = section_name.rsplit('_', 1)
            if len(parts) == 2:
                try:
                    area_cm2 = float(parts[1])
                    group = f"{round(area_cm2 * 100)}"   # cm² → mm²
                except ValueError:
                    group = section_name
            else:
                group = section_name

        if group not in groups:
            groups[group] = []
        groups[group].append(idx)

    group_order = sorted(groups.keys(), key=_natural_sort_key)
    return groups, group_order


# ---------------------------------------------------------------------------
#  CHS property estimation for continuous mode
# ---------------------------------------------------------------------------

def chs_properties_from_area(A_mm2, dt_ratio=25):
    """
    Estimate CHS section properties from cross-section area.
    Assumes a d/t ratio to compute diameter, thickness, I, and i.

    For thin-walled CHS: A ≈ π · d · t
    With d/t = k: t = d/k, A = π · d · (d/k) → d = sqrt(A · k / π)
    """
    k = dt_ratio
    d = math.sqrt(A_mm2 * k / math.pi)
    t = d / k
    d_inner = d - 2 * t
    I = math.pi / 64 * (d**4 - d_inner**4)
    i = math.sqrt(I / A_mm2)
    return {'d': d, 't': t, 'A': A_mm2, 'I': I, 'i': i}


# ---------------------------------------------------------------------------
#  Deterministic EC3 constraint functions
# ---------------------------------------------------------------------------

def check_tension_unity(N_Ed, A, fy=FY, gamma_M0=GAMMA_M0):
    """
    §6.2.3 Tension resistance: N_Ed / N_pl,Rd
    Returns unity ratio (≤ 1.0 means OK). Returns 0 if not in tension.
    """
    if N_Ed <= 0:
        return 0.0
    N_pl_Rd = A * fy / gamma_M0
    return N_Ed / N_pl_Rd


def check_compression_unity(N_Ed, A, fy=FY, gamma_M0=GAMMA_M0):
    """
    §6.2.4 Compression resistance: |N_Ed| / N_c,Rd
    Returns unity ratio. Returns 0 if not in compression.
    """
    if N_Ed >= 0:
        return 0.0
    N_c_Rd = A * fy / gamma_M0
    return abs(N_Ed) / N_c_Rd


def check_buckling_unity(N_Ed, A, L_cr, i, fy=FY, E=E, gamma_M1=GAMMA_M1, alpha=0.21):
    """
    §6.3.1 Flexural buckling: |N_Ed| / N_b,Rd
    - alpha = 0.21 for CHS hot-finished (buckling curve 'a', Table 6.2)
    - lambda_bar = (L_cr / i) / (π · sqrt(E / fy))
    - Phi = 0.5 · [1 + alpha·(lambda_bar - 0.2) + lambda_bar²]
    - chi = 1 / (Phi + sqrt(Phi² - lambda_bar²)), chi ≤ 1.0
    - N_b,Rd = chi · A · fy / gamma_M1
    Returns unity ratio. Returns 0 if not in compression.
    """
    if N_Ed >= 0:
        return 0.0
    if i <= 0:
        return 999.0

    lambda_1 = math.pi * math.sqrt(E / fy)
    lambda_bar = (L_cr / i) / lambda_1

    if lambda_bar <= 0.2:
        chi = 1.0
    else:
        Phi = 0.5 * (1 + alpha * (lambda_bar - 0.2) + lambda_bar**2)
        chi = 1.0 / (Phi + math.sqrt(max(Phi**2 - lambda_bar**2, 1e-10)))
        chi = min(chi, 1.0)

    N_b_Rd = chi * A * fy / gamma_M1
    return abs(N_Ed) / N_b_Rd


def check_slenderness(L_cr, i, limit=200):
    """
    Slenderness ratio check for compression members.
    Returns lambda / limit (> 1.0 means violation).
    """
    if i <= 0:
        return 999.0
    return (L_cr / i) / limit


def check_deflection_unity(max_disp_mm, span_mm, limit_ratio=250):
    """
    §7.2 Serviceability: max_displacement / (span / limit_ratio)
    Returns ratio (> 1.0 means violation).
    """
    if span_mm <= 0:
        return 0.0
    allowable = span_mm / limit_ratio
    return max_disp_mm / allowable


# ---------------------------------------------------------------------------
#  Span length auto-detection
# ---------------------------------------------------------------------------

def compute_span_length(truss_data):
    """
    Estimate truss span as the maximum distance between any two
    pinned/fixed support joints.
    """
    bc_joints = []
    for bc_id, joint_id, bc_type, constraints in truss_data['boundary_conditions']:
        if joint_id in truss_data['joints']:
            bc_joints.append(truss_data['joints'][joint_id])

    if len(bc_joints) < 2:
        return 0.0

    max_dist = 0.0
    for i in range(len(bc_joints)):
        for j in range(i + 1, len(bc_joints)):
            dx = bc_joints[i][0] - bc_joints[j][0]
            dy = bc_joints[i][1] - bc_joints[j][1]
            dz = bc_joints[i][2] - bc_joints[j][2]
            dist = math.sqrt(dx**2 + dy**2 + dz**2)
            if dist > max_dist:
                max_dist = dist

    return max_dist * 1000.0  # m -> mm


# ---------------------------------------------------------------------------
#  Compute element lengths
# ---------------------------------------------------------------------------

def compute_element_lengths(truss_data):
    """
    Compute element lengths from joint coordinates.
    Returns dict: {elem_index: length_mm}
    """
    joints = truss_data['joints']
    lengths = {}
    for idx, (elem_id, start, end, section_name) in enumerate(truss_data['elements']):
        dx = end[0] - start[0]
        dy = end[1] - start[1]
        dz = end[2] - start[2]
        lengths[idx] = math.sqrt(dx**2 + dy**2 + dz**2) * 1000.0  # m -> mm
    return lengths


# ---------------------------------------------------------------------------
#  pymoo Problem Definition
# ---------------------------------------------------------------------------

class TrussOptimizationProblem(ElementwiseProblem):

    def __init__(self, truss_data, section_catalog, section_groups, group_order,
                 element_lengths, span_length, mode="discrete",
                 is_geom_nonlin=True, is_mat_nonlin=True):
        """
        Parameters:
        -----------
        truss_data : dict from read_truss_file()
        section_catalog : list of dicts with CHS properties (sorted by area)
        section_groups : dict {group_name: [elem_indices]}
        group_order : list of group names
        element_lengths : dict {elem_index: length_mm}
        span_length : float, truss span in mm for deflection check
        mode : "discrete" or "continuous"
        """
        self.truss_data = truss_data
        self.catalog = section_catalog
        self.groups = section_groups
        self.group_order = group_order
        self.elem_lengths = element_lengths
        self.span_length = span_length
        self.mode = mode
        self.is_geom_nonlin = is_geom_nonlin
        self.is_mat_nonlin = is_mat_nonlin

        self.n_groups = len(group_order)
        self.n_elements = len(truss_data['elements'])

        # Constraints: per element (strength, buckling, slenderness) + 1 deflection
        n_constr = 3 * self.n_elements + 1

        if mode == "discrete":
            xl = np.zeros(self.n_groups)
            xu = np.full(self.n_groups, len(section_catalog) - 1)
            super().__init__(
                n_var=self.n_groups, n_obj=1,
                n_ieq_constr=n_constr,
                xl=xl, xu=xu, vtype=int
            )
        else:
            # Continuous: area in mm² (50 mm² to 30000 mm²)
            xl = np.full(self.n_groups, 50.0)
            xu = np.full(self.n_groups, 30000.0)
            super().__init__(
                n_var=self.n_groups, n_obj=1,
                n_ieq_constr=n_constr,
                xl=xl, xu=xu, vtype=float
            )

    def _get_section_props(self, x):
        """
        Map design variables to section properties per group.
        Returns dict: {group_name: {'A': mm², 'I': mm⁴, 'i': mm, 'ID': str}}
        """
        props = {}
        for k, group_name in enumerate(self.group_order):
            if self.mode == "discrete":
                idx = int(np.clip(np.round(x[k]), 0, len(self.catalog) - 1))
                sec = self.catalog[idx]
                props[group_name] = sec
            else:
                area = float(x[k])
                derived = chs_properties_from_area(area)
                derived['ID'] = f"CHS_cont_{area:.0f}"
                props[group_name] = derived
        return props

    def _build_section_areas(self, group_props):
        """
        Build {section_name: area_mm2} dict for all elements,
        mapping each element's original section name to its group's area.
        """
        section_areas = {}
        for group_name, elem_indices in self.groups.items():
            area = group_props[group_name]['A']
            for idx in elem_indices:
                orig_section_name = self.truss_data['elements'][idx][3]
                section_areas[orig_section_name] = area
        return section_areas

    def _evaluate(self, x, out, *args, **kwargs):
        group_props = self._get_section_props(x)
        section_areas = self._build_section_areas(group_props)

        # Compute mass objective (kg)
        # mass = sum(rho * A * L) where A in m², L in m
        total_mass = 0.0
        for group_name, elem_indices in self.groups.items():
            A_mm2 = group_props[group_name]['A']
            A_m2 = A_mm2 * 1e-6
            for idx in elem_indices:
                L_mm = self.elem_lengths[idx]
                L_m = L_mm * 1e-3
                total_mass += RHO_STEEL * A_m2 * L_m

        # Run structural analysis
        try:
            result = run_analysis_in_memory(
                self.truss_data, section_areas,
                self.is_geom_nonlin, self.is_mat_nonlin
            )
        except Exception:
            # Analysis failed — penalize heavily
            out["F"] = [1e10]
            out["G"] = [1e6] * self.n_constr
            return

        if not result['success']:
            out["F"] = [1e10]
            out["G"] = [1e6] * self.n_constr
            return

        # Build elem_index → group mapping
        idx_to_group = {}
        for group_name, indices in self.groups.items():
            for idx in indices:
                idx_to_group[idx] = group_name

        # Evaluate constraints (pymoo convention: g ≤ 0 is feasible)
        constraints = []
        max_disp = 0.0

        for idx, (elem_id, start, end, section_name) in enumerate(self.truss_data['elements']):
            group_name = idx_to_group[idx]
            sec = group_props[group_name]
            A = sec['A']
            I = sec['I']
            i = sec['i']
            L_cr = self.elem_lengths[idx]

            N_Ed = result['member_forces'].get(elem_id, 0.0)

            # Strength unity (tension or compression) - 1.0
            if N_Ed > 0:
                strength = check_tension_unity(N_Ed, A) - 1.0
            elif N_Ed < 0:
                strength = check_compression_unity(N_Ed, A) - 1.0
            else:
                strength = -1.0
            constraints.append(strength)

            # Buckling unity - 1.0
            buckling = check_buckling_unity(N_Ed, A, L_cr, i) - 1.0
            constraints.append(buckling)

            # Slenderness - 1.0 (only for compression)
            if N_Ed < 0:
                slender = check_slenderness(L_cr, i) - 1.0
            else:
                slender = -1.0
            constraints.append(slender)

        # Max displacement for deflection check
        for joint_id, (dx, dy, dz) in result['joint_displacements'].items():
            disp = math.sqrt(dx**2 + dy**2 + dz**2)
            if disp > max_disp:
                max_disp = disp

        defl_unity = check_deflection_unity(max_disp, self.span_length) - 1.0
        constraints.append(defl_unity)

        out["F"] = [total_mass]
        out["G"] = constraints


# ---------------------------------------------------------------------------
#  Optimization runner
# ---------------------------------------------------------------------------

def optimize_truss(truss_file="truss.txt",
                   section_csv="CHS_section.csv",
                   mode="discrete",
                   algorithm="PSO",
                   pop_size=50,
                   n_gen=100,
                   seed=42,
                   is_geom_nonlin=True,
                   is_mat_nonlin=True):
    """
    Run structural optimization of truss sections.

    Parameters:
    -----------
    truss_file : str
        Path to truss.txt
    section_csv : str
        Path to CHS section catalog CSV
    mode : str
        "discrete" (pick from catalog) or "continuous" (optimize area)
    algorithm : str
        "PSO" (default) or "GA"
    pop_size : int
        Population size
    n_gen : int
        Number of generations
    seed : int
        Random seed for reproducibility
    is_geom_nonlin : bool
        Use geometric nonlinearity in analysis
    is_mat_nonlin : bool
        Use material nonlinearity in analysis

    Returns:
    --------
    dict with keys:
        'success': bool
        'best_mass': float (kg)
        'initial_mass': float (kg)
        'section_assignments': dict {group_name: section_ID}
        'constraint_summary': dict with max unity ratios
        'n_evaluations': int
        'report': str (formatted text report)
    """
    base_dir = os.path.dirname(os.path.abspath(__file__))
    truss_path = os.path.join(base_dir, truss_file)
    csv_path = os.path.join(base_dir, section_csv)

    # Load data
    truss_data = read_truss_file(truss_path)
    catalog = load_section_catalog(csv_path)

    if not catalog:
        return {'success': False, 'report': "No valid CHS sections found in catalog."}

    eurocode_csv = os.path.join(base_dir, "CHS_eurocode.csv")
    area_lookup = load_area_lookup(eurocode_csv) if os.path.exists(eurocode_csv) else {}
    groups, group_order = get_section_groups(truss_data, area_lookup)
    elem_lengths = compute_element_lengths(truss_data)
    span_length = compute_span_length(truss_data)

    print(f"Optimization setup:")
    print(f"  Mode: {mode}")
    print(f"  Algorithm: {algorithm}")
    print(f"  Section groups: {len(group_order)} ({', '.join(group_order)})")
    print(f"  Elements: {len(truss_data['elements'])}")
    print(f"  Catalog sections: {len(catalog)}")
    print(f"  Span length: {span_length:.1f} mm")
    print(f"  Population: {pop_size}, Generations: {n_gen}")

    # Compute initial mass and check initial feasibility
    initial_mass, initial_feasibility = compute_initial_mass_and_feasibility(
        truss_data, groups, group_order, elem_lengths, span_length, csv_path,
        is_geom_nonlin, is_mat_nonlin,
        area_lookup=area_lookup
    )

    # Create problem
    problem = TrussOptimizationProblem(
        truss_data=truss_data,
        section_catalog=catalog,
        section_groups=groups,
        group_order=group_order,
        element_lengths=elem_lengths,
        span_length=span_length,
        mode=mode,
        is_geom_nonlin=is_geom_nonlin,
        is_mat_nonlin=is_mat_nonlin,
    )

    # Select algorithm(s)
    def _make_algo(name, pop):
        name = name.upper()
        if name == "PSO":
            return PSO(pop_size=pop)
        elif name == "GA":
            return GA(pop_size=pop)
        elif name == "DE":
            return DE(pop_size=pop)
        else:
            return PSO(pop_size=pop)

    termination = get_termination("n_gen", n_gen)

    if algorithm.upper() == "ALL":
        algo_names = ["PSO", "GA", "DE"]
    else:
        algo_names = [algorithm.upper()]

    best_res = None
    best_algo_name = None
    all_algo_results = {}  # {algo_name: mass_kg or None}

    for algo_name in algo_names:
        algo = _make_algo(algo_name, pop_size)
        print(f"\nRunning {algo_name} optimization...")
        res = minimize(
            problem, algo,
            termination=termination,
            seed=seed,
            verbose=True,
        )
        if res.X is not None:
            all_algo_results[algo_name] = res.F[0]
            print(f"  -> {algo_name} best mass: {res.F[0]:.3f} kg")
            if best_res is None or res.F[0] < best_res.F[0]:
                best_res = res
                best_algo_name = algo_name
        else:
            all_algo_results[algo_name] = None
            print(f"  -> {algo_name}: no feasible solution found")

    if len(algo_names) > 1 and best_algo_name:
        print(f"\nBest algorithm: {best_algo_name}")
        algorithm = f"ALL (best: {best_algo_name})"
    elif len(algo_names) == 1:
        best_algo_name = algo_names[0]

    res = best_res

    # Extract results
    if res is None or res.X is None:
        return {
            'success': False,
            'report': "Optimization did not find a feasible solution. "
                      "Try increasing pop_size or n_gen, or relaxing constraints."
        }

    best_x = res.X
    best_mass = res.F[0]
    group_props = problem._get_section_props(best_x)

    # Build section assignments
    assignments = {}
    for group_name in group_order:
        sec = group_props[group_name]
        assignments[group_name] = {
            'section_id': sec.get('ID', f"A={sec['A']:.0f}mm2"),
            'A_mm2': sec['A'],
            'I_mm4': sec['I'],
            'i_mm': sec['i'],
        }

    # Evaluate constraints at optimum for reporting
    section_areas = problem._build_section_areas(group_props)
    try:
        final_result = run_analysis_in_memory(
            truss_data, section_areas,
            is_geom_nonlin, is_mat_nonlin
        )
    except Exception:
        final_result = None

    constraint_summary = compute_constraint_summary(
        truss_data, group_props, groups, elem_lengths, span_length, final_result
    )

    # Save optimized truss file
    optimized_path = save_optimized_truss_file(
        truss_path, truss_data, group_order, groups, assignments
    )

    # Build report
    report = format_report(
        mode, algorithm, group_order, assignments,
        best_mass, initial_mass, initial_feasibility, constraint_summary,
        span_length, catalog, res, pop_size, n_gen, optimized_path,
        all_algo_results=all_algo_results
    )

    return {
        'success': True,
        'best_mass': best_mass,
        'initial_mass': initial_mass,
        'section_assignments': assignments,
        'constraint_summary': constraint_summary,
        'n_evaluations': res.algorithm.n_gen * pop_size if res.algorithm else 0,
        'report': report,
        'optimized_file': optimized_path,
    }


# ---------------------------------------------------------------------------
#  Helper functions
# ---------------------------------------------------------------------------

def compute_initial_mass_and_feasibility(truss_data, groups, group_order,
                                         elem_lengths, span_length, csv_path,
                                         is_geom_nonlin=True, is_mat_nonlin=True,
                                         area_lookup=None):
    """
    Compute mass of the initial truss and check EC3 feasibility.
    Returns (mass_kg, feasibility_dict).

    area_lookup: {section_id: A_mm2} from CHS_eurocode.csv — used as
    fallback when the section is not in the main CHS catalog (csv_path).
    """
    from truss3D_analysis_from_file import load_chs_properties

    all_sections = load_chs_properties(csv_path)

    # Compute mass — try catalog first, then area_lookup from eurocode CSV
    total_mass = 0.0
    section_areas = {}
    for idx, (elem_id, start, end, section_name) in enumerate(truss_data['elements']):
        if section_name in all_sections:
            A_mm2 = all_sections[section_name]['A']
        elif area_lookup and section_name in area_lookup:
            A_mm2 = area_lookup[section_name]
        else:
            continue
        L_mm = elem_lengths[idx]
        total_mass += RHO_STEEL * (A_mm2 * 1e-6) * (L_mm * 1e-3)
        section_areas[section_name] = A_mm2

    # Run analysis on initial design to check feasibility
    n_violations = 0
    max_unity = 0.0
    feasible = False
    try:
        result = run_analysis_in_memory(truss_data, section_areas,
                                        is_geom_nonlin, is_mat_nonlin)
        if result['success']:
            # Load catalog for I, i properties (needed for buckling check)
            catalog = load_section_catalog(csv_path)
            # Build a lookup: for initial sections, try to find matching catalog entry
            # or use estimated properties
            for idx, (elem_id, start, end, section_name) in enumerate(truss_data['elements']):
                A = section_areas.get(section_name, 0)
                if A <= 0:
                    continue
                N_Ed = result['member_forces'].get(elem_id, 0.0)
                L_cr = elem_lengths[idx]

                # Estimate i from area (no catalog match for custom sections)
                props = chs_properties_from_area(A)
                i = props['i']

                if N_Ed > 0:
                    u = check_tension_unity(N_Ed, A)
                elif N_Ed < 0:
                    u = max(check_compression_unity(N_Ed, A),
                            check_buckling_unity(N_Ed, A, L_cr, i))
                else:
                    u = 0.0

                max_unity = max(max_unity, u)
                if u > 1.0:
                    n_violations += 1

            # Check deflection
            max_disp = 0.0
            for jid, (dx, dy, dz) in result['joint_displacements'].items():
                disp = math.sqrt(dx**2 + dy**2 + dz**2)
                max_disp = max(max_disp, disp)
            defl_u = check_deflection_unity(max_disp, span_length)
            max_unity = max(max_unity, defl_u)
            if defl_u > 1.0:
                n_violations += 1

            feasible = (n_violations == 0)
    except Exception:
        pass

    feasibility = {
        'feasible': feasible,
        'n_violations': n_violations,
        'max_unity': max_unity,
    }
    return total_mass, feasibility


def compute_constraint_summary(truss_data, group_props, groups, elem_lengths,
                               span_length, analysis_result):
    """Compute max unity ratios at the optimal solution."""
    idx_to_group = {}
    for group_name, indices in groups.items():
        for idx in indices:
            idx_to_group[idx] = group_name

    max_tension = 0.0
    max_compression = 0.0
    max_buckling = 0.0
    max_slenderness = 0.0
    max_deflection = 0.0
    critical_elem_buckling = None

    if analysis_result and analysis_result['success']:
        for idx, (elem_id, start, end, section_name) in enumerate(truss_data['elements']):
            group_name = idx_to_group[idx]
            sec = group_props[group_name]
            N_Ed = analysis_result['member_forces'].get(elem_id, 0.0)
            L_cr = elem_lengths[idx]

            t = check_tension_unity(N_Ed, sec['A'])
            c = check_compression_unity(N_Ed, sec['A'])
            b = check_buckling_unity(N_Ed, sec['A'], L_cr, sec['i'])
            if N_Ed < 0:
                s = check_slenderness(L_cr, sec['i'])
            else:
                s = 0.0

            max_tension = max(max_tension, t)
            max_compression = max(max_compression, c)
            if b > max_buckling:
                max_buckling = b
                critical_elem_buckling = elem_id
            max_slenderness = max(max_slenderness, s)

        max_disp = 0.0
        for jid, (dx, dy, dz) in analysis_result['joint_displacements'].items():
            disp = math.sqrt(dx**2 + dy**2 + dz**2)
            max_disp = max(max_disp, disp)
        max_deflection = check_deflection_unity(max_disp, span_length)

    return {
        'max_tension_unity': max_tension,
        'max_compression_unity': max_compression,
        'max_buckling_unity': max_buckling,
        'critical_buckling_elem': critical_elem_buckling,
        'max_slenderness_ratio': max_slenderness,
        'max_deflection_unity': max_deflection,
        'all_satisfied': all([
            max_tension <= 1.0,
            max_compression <= 1.0,
            max_buckling <= 1.0,
            max_slenderness <= 1.0,
            max_deflection <= 1.0,
        ]),
    }


def format_report(mode, algorithm, group_order, assignments, best_mass,
                   initial_mass, initial_feasibility, constraint_summary,
                   span_length, catalog, res, pop_size, n_gen,
                   optimized_path=None, all_algo_results=None):
    """Format optimization results as a human-readable report."""
    lines = []
    lines.append("=" * 70)
    lines.append("TRUSS STRUCTURAL OPTIMIZATION REPORT")
    lines.append("=" * 70)
    lines.append("")
    lines.append(f"Mode: {mode}")
    lines.append(f"Algorithm: {algorithm} (pop={pop_size}, gen={n_gen})")
    lines.append(f"Span length: {span_length:.1f} mm")
    lines.append(f"Steel density: {RHO_STEEL} kg/m3, fy={FY} MPa, E={E} MPa")
    lines.append("")

    # Algorithm comparison table (for ALL mode)
    if all_algo_results and len(all_algo_results) > 1:
        lines.append("-" * 70)
        lines.append("ALGORITHM COMPARISON")
        lines.append("-" * 70)
        lines.append(f"{'Algorithm':<25} {'Best mass (kg)':<18} {'Status'}")
        lines.append("-" * 55)
        # Sort by mass (feasible first, then by mass)
        for name in ["PSO", "GA", "DE"]:
            if name not in all_algo_results:
                continue
            mass = all_algo_results[name]
            if mass is not None:
                is_best = " <-- BEST" if mass == best_mass else ""
                lines.append(f"{name:<25} {mass:<18.3f} feasible{is_best}")
            else:
                lines.append(f"{name:<25} {'---':<18} no feasible solution")
        lines.append("")

    # Mass comparison
    lines.append("-" * 70)
    lines.append("MASS SUMMARY")
    lines.append("-" * 70)

    initial_ok = initial_feasibility.get('feasible', False)
    if initial_ok:
        lines.append(f"Initial mass:   {initial_mass:.3f} kg (EC3 feasible)")
    else:
        n_viol = initial_feasibility.get('n_violations', 0)
        max_u = initial_feasibility.get('max_unity', 0)
        lines.append(f"Initial mass:   {initial_mass:.3f} kg (INFEASIBLE - {n_viol} EC3 violations, max unity={max_u:.2f})")

    lines.append(f"Optimized mass: {best_mass:.3f} kg (minimum feasible)")
    if initial_mass > 0:
        if best_mass <= initial_mass and initial_ok:
            reduction = (1 - best_mass / initial_mass) * 100
            lines.append(f"Mass reduction: {reduction:.1f}%")
        elif not initial_ok:
            lines.append(f"Note: Initial design violated EC3 constraints.")
            lines.append(f"      Optimized mass is the lightest design satisfying all checks.")
        else:
            reduction = (1 - best_mass / initial_mass) * 100
            lines.append(f"Mass reduction: {reduction:.1f}%")
    lines.append("")

    # Section assignments
    lines.append("-" * 70)
    lines.append("OPTIMAL SECTION ASSIGNMENTS")
    lines.append("-" * 70)
    lines.append(f"{'Group':<8} {'Initial A (mm2)':<18} {'Optimized Section':<25} {'Opt. A (mm2)':<12}")
    lines.append("-" * 63)
    for i, group_name in enumerate(group_order, 1):
        a = assignments[group_name]
        try:
            init_a_str = f"{float(group_name):.1f}"
        except ValueError:
            init_a_str = group_name
        lines.append(f"{i:<8} {init_a_str:<18} {a['section_id']:<25} {a['A_mm2']:<12.1f}")
    lines.append("")

    # Constraint summary
    lines.append("-" * 70)
    lines.append("EC3 CONSTRAINT VERIFICATION")
    lines.append("-" * 70)
    cs = constraint_summary
    status = lambda v: "OK" if v <= 1.0 else "VIOLATED"
    lines.append(f"Tension (6.2.3):       max unity = {cs['max_tension_unity']:.3f}  [{status(cs['max_tension_unity'])}]")
    lines.append(f"Compression (6.2.4):   max unity = {cs['max_compression_unity']:.3f}  [{status(cs['max_compression_unity'])}]")
    buck_info = f" (elem {cs['critical_buckling_elem']})" if cs['critical_buckling_elem'] else ""
    lines.append(f"Buckling (6.3.1):      max unity = {cs['max_buckling_unity']:.3f}  [{status(cs['max_buckling_unity'])}]{buck_info}")
    lines.append(f"Slenderness (L/i/200):   max ratio = {cs['max_slenderness_ratio']:.3f}  [{status(cs['max_slenderness_ratio'])}]")
    lines.append(f"Deflection (L/250):    max ratio = {cs['max_deflection_unity']:.3f}  [{status(cs['max_deflection_unity'])}]")
    lines.append("")

    all_ok = cs['all_satisfied']
    lines.append(f"Overall: {'ALL CONSTRAINTS SATISFIED' if all_ok else 'SOME CONSTRAINTS VIOLATED'}")
    lines.append("")

    if optimized_path:
        lines.append(f"Optimized truss saved to: {optimized_path}")
        lines.append("Use 'draw optimized truss' to draw it in AutoCAD.")

    lines.append("=" * 70)

    return "\n".join(lines)


# ---------------------------------------------------------------------------
#  Save optimized truss file
# ---------------------------------------------------------------------------

# ACI color palette for assigning distinct layer colors
_ACI_COLORS = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 30, 40, 50,
               60, 70, 80, 90, 100, 110, 120, 130, 140, 150, 160, 170, 180]


def save_optimized_truss_file(truss_path, truss_data, group_order, groups,
                              assignments, output_path=None):
    """
    Save a copy of truss.txt with optimized section assignments.

    The optimized file has the same joints, loads, and BCs but element section
    names are replaced with the optimal CHS sections (e.g., 'CHS_219.1x6.3').
    Layer colors in DRAWING INFO are updated accordingly.

    Parameters:
    -----------
    truss_path : str
        Path to original truss.txt
    truss_data : dict
        Parsed truss data from read_truss_file()
    group_order : list of str
        Ordered group names
    groups : dict
        {group_name: [elem_indices]}
    assignments : dict
        {group_name: {'section_id': str, 'A_mm2': float, ...}}
    output_path : str or None
        Output file path. Defaults to truss_optimized.txt next to truss_path.

    Returns:
    --------
    str : Path to saved file
    """
    if output_path is None:
        base_dir = os.path.dirname(truss_path)
        output_path = os.path.join(base_dir, "truss_optimized.txt")

    # Build mapping: original section name -> new section name
    # Use actual CHS section ID (e.g. "CHS_76.1x3.2") when available
    section_map = {}  # old_section_name -> new_section_name
    for group_name, elem_indices in groups.items():
        assignment = assignments[group_name]
        if 'section_id' in assignment and assignment['section_id']:
            new_section_name = assignment['section_id']
        else:
            new_area_cm2 = assignment['A_mm2'] / 100.0
            new_section_name = f"{group_name}_{new_area_cm2:.3f}"
        for idx in elem_indices:
            old_section_name = truss_data['elements'][idx][3]
            section_map[old_section_name] = new_section_name

    # Collect unique new section names and assign colors
    unique_new_sections = sorted(set(section_map.values()))
    color_map = {}
    for i, sec_name in enumerate(unique_new_sections):
        color_map[sec_name] = _ACI_COLORS[i % len(_ACI_COLORS)]

    # Read original file
    with open(truss_path, 'r') as f:
        original_lines = f.readlines()

    # Two-pass approach:
    # Pass 1: Identify DRAWING INFO section boundaries
    # Pass 2: Rewrite with updated layer_color and element sections

    output_lines = []
    current_section = None
    drawing_info_other_lines = []  # non-layer_color lines in DRAWING INFO
    after_section_title = False  # True when we just saw a section title line

    for line in original_lines:
        stripped = line.strip()

        # Detect section title lines (contain the section name, not just ===)
        if 'DRAWING INFO' in stripped and not stripped.startswith('='):
            current_section = 'drawing_info_title'
            output_lines.append(line)
            continue
        elif current_section == 'drawing_info_title' and stripped.startswith('='):
            # This is the === separator after the title
            current_section = 'drawing_info'
            output_lines.append(line)
            continue
        elif 'TRUSS JOINT COORDINATES' in stripped:
            current_section = 'joints'
            output_lines.append(line)
            continue
        elif 'TRUSS ELEMENT INFORMATION' in stripped:
            current_section = 'elements'
            output_lines.append(line)
            continue
        elif 'APPLIED LOADS' in stripped:
            current_section = 'loads'
            output_lines.append(line)
            continue
        elif 'BOUNDARY CONDITIONS' in stripped:
            current_section = 'bc'
            output_lines.append(line)
            continue

        # Handle DRAWING INFO section
        if current_section == 'drawing_info':
            if stripped.startswith('layer_color='):
                # Skip original layer_color lines
                continue
            elif stripped.startswith('='):
                # Closing separator — write non-layer_color lines first,
                # then new layer_color lines, then the separator
                for other_line in drawing_info_other_lines:
                    output_lines.append(other_line)
                for sec_name in unique_new_sections:
                    color = color_map[sec_name]
                    output_lines.append(f"layer_color={sec_name},{color}\n")
                output_lines.append(line)
                current_section = None
                drawing_info_other_lines = []
                continue
            else:
                # Keep other lines (e.g. joint_block=)
                drawing_info_other_lines.append(line)
                continue

        # Handle ELEMENT INFORMATION section: replace section names
        if current_section == 'elements':
            if stripped.startswith('=') or stripped.startswith('-') or not stripped:
                output_lines.append(line)
                continue
            if stripped.startswith('Elem'):
                output_lines.append(line)
                continue
            # Data line: replace section name
            parts = stripped.split()
            if len(parts) >= 9:
                old_section = parts[8]
                new_section = section_map.get(old_section, old_section)
                # Reconstruct the line with same formatting
                output_lines.append(
                    f"{parts[0]:<10} {parts[1]:>10} {parts[2]:>10} {parts[3]:>10} "
                    f"{parts[4]:>10} {parts[5]:>10} {parts[6]:>10} {parts[7]:>10} "
                    f"{new_section:<15}\n"
                )
                continue

        output_lines.append(line)

    with open(output_path, 'w') as f:
        f.writelines(output_lines)

    return output_path


# ---------------------------------------------------------------------------
#  Main entry point (for standalone testing)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    result = optimize_truss(
        truss_file="truss.txt",
        section_csv="CHS_section.csv",
        mode="discrete",
        algorithm="PSO",
        pop_size=30,
        n_gen=50,
        seed=42,
    )
    print("\n" + result['report'])
