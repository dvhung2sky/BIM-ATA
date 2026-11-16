"""
3D Truss Analysis with Geometrical and Material Nonlinearity using PyOpenSees
Reads truss geometry from truss.txt
Section properties from CHS_eurocode.csv
Results exported to result.txt
"""

import openseespy.opensees as ops
import math
import numpy as np
import csv
import os


def load_chs_properties(csv_path):
    """
    Load section properties from CSV file (CHS and custom sections).

    Returns dict: {section_name: {'A': area_mm2}}
    """
    sections = {}
    with open(csv_path, 'r') as f:
        reader = csv.DictReader(f)
        for row in reader:
            section_id = row['ID']
            if section_id == '[]':
                continue
            try:
                a_val = row['A'].strip()
                if a_val:
                    sections[section_id] = {
                        'A': float(a_val) * 100,  # cm² to mm²
                    }
            except (ValueError, KeyError):
                continue
    return sections


def read_truss_file(file_path):
    """
    Read truss data from a formatted text file.

    Parameters:
    -----------
    file_path : str
        Path to the truss.txt file

    Returns:
    --------
    dict with keys:
        'joints': {joint_id: (x, y, z), ...}
        'elements': [(elem_id, start_coords, end_coords, section_name), ...]
        'loads': [(load_id, joint_id, magnitude, direction, fx, fy, fz, load_type), ...]
        'boundary_conditions': [(bc_id, joint_id, bc_type, constraints), ...]
    """
    with open(file_path, 'r') as f:
        content = f.read()

    data = {
        'joints': {},
        'elements': [],
        'loads': [],
        'boundary_conditions': []
    }

    lines = content.split('\n')
    current_section = None

    for line in lines:
        line = line.strip()

        if 'TRUSS JOINT COORDINATES' in line:
            current_section = 'joints'
            continue
        elif 'TRUSS ELEMENT INFORMATION' in line:
            current_section = 'elements'
            continue
        elif 'APPLIED LOADS' in line:
            current_section = 'loads'
            continue
        elif 'BOUNDARY CONDITIONS' in line:
            current_section = 'boundary_conditions'
            continue

        if line.startswith('=') or line.startswith('-') or not line:
            continue
        if line.startswith('Joint') or line.startswith('Elem') or line.startswith('Load') or line.startswith('BC'):
            if 'X' in line and 'Y' in line:
                continue

        if current_section == 'joints':
            parts = line.split()
            if len(parts) >= 4:
                try:
                    joint_id = int(parts[0])
                    x, y, z = float(parts[1]), float(parts[2]), float(parts[3])
                    data['joints'][joint_id] = (x, y, z)
                except ValueError:
                    continue

        elif current_section == 'elements':
            parts = line.split()
            if len(parts) >= 9:
                try:
                    elem_id = int(parts[0])
                    start_coords = (float(parts[1]), float(parts[2]), float(parts[3]))
                    end_coords = (float(parts[4]), float(parts[5]), float(parts[6]))
                    section_name = parts[8]
                    data['elements'].append((elem_id, start_coords, end_coords, section_name))
                except ValueError:
                    continue

        elif current_section == 'loads':
            parts = line.split()
            if len(parts) >= 8:
                try:
                    load_id = int(parts[0])
                    load_type = parts[1]
                    magnitude = float(parts[2])
                    # New format: Load Type Value JointID Dir Fx Fy Fz X Y Z
                    joint_id = int(parts[3])
                    direction = parts[4]          # e.g. 'X', 'Y', 'Z'
                    fx = float(parts[5])          # kN
                    fy = float(parts[6])          # kN
                    fz = float(parts[7])          # kN
                    data['loads'].append((load_id, joint_id, magnitude, direction, fx, fy, fz, load_type))
                except (ValueError, IndexError):
                    continue

        elif current_section == 'boundary_conditions':
            parts = line.split()
            if len(parts) >= 6:
                try:
                    bc_id = int(parts[0])
                    bc_type = parts[1]
                    joint_id = int(parts[5])
                    constraints_str = parts[6] if len(parts) > 6 else ""
                    constraints = [int(c) for c in constraints_str.split(',') if c.isdigit()]
                    data['boundary_conditions'].append((bc_id, joint_id, bc_type, constraints))
                except ValueError:
                    continue

    return data


def find_joint_by_coords(x, y, z, joints, tol=0.5):
    """Find joint ID matching coordinates within tolerance"""
    for jid, (jx, jy, jz) in joints.items():
        if abs(x - jx) < tol and abs(y - jy) < tol and abs(z - jz) < tol:
            return jid
    return None


def run_analysis_in_memory(truss_data, section_areas, is_geom_nonlin=True, is_mat_nonlin=True):
    """
    Run 3D truss analysis with in-memory data (no file I/O).
    Used by the optimizer for fast repeated evaluations.

    Parameters:
    -----------
    truss_data : dict
        Parsed truss data from read_truss_file()
    section_areas : dict
        Section areas {section_name: area_mm2}
    is_geom_nonlin : bool
        If True, use CorotTruss for large displacement analysis
    is_mat_nonlin : bool
        If True, use Steel01 material for material nonlinearity

    Returns:
    --------
    dict with keys:
        'success': bool
        'joint_displacements': {joint_id: (dx, dy, dz)}
        'member_forces': {elem_id: axial_force_N}
        'member_stresses': {elem_id: stress_MPa}
        'elements': [(elem_id, node_i, node_j, section_name), ...]
    """
    joints = truss_data['joints']
    element_data = truss_data['elements']
    load_data = truss_data['loads']
    bc_data = truss_data['boundary_conditions']

    ops.wipe()
    ops.model('basic', '-ndm', 3, '-ndf', 3)

    for joint_id, (x, y, z) in sorted(joints.items()):
        ops.node(joint_id, x * 1000, y * 1000, z * 1000)   # m → mm (E in MPa, A in mm²)

    for bc_id, joint_id, bc_type, constraints in bc_data:
        fix_x = 1 if 1 in constraints else 0
        fix_y = 1 if 2 in constraints else 0
        fix_z = 1 if 3 in constraints else 0
        ops.fix(joint_id, fix_x, fix_y, fix_z)

    E = 210000.0   # MPa — Eurocode steel (matches SAP2000's 2.1e8 kN/m²)
    Fy = 355.0

    section_names = list(set(elem[3] for elem in element_data))
    section_names.sort()
    section_to_mat = {name: i + 1 for i, name in enumerate(section_names)}

    for name, mat_tag in section_to_mat.items():
        if is_mat_nonlin:
            ops.uniaxialMaterial("Steel01", mat_tag, Fy, E, 0.02)
        else:
            ops.uniaxialMaterial("Elastic", mat_tag, E)

    elements = []
    for elem_id, start_coords, end_coords, section_name in element_data:
        node_i = find_joint_by_coords(*start_coords, joints)
        node_j = find_joint_by_coords(*end_coords, joints)
        if node_i is None or node_j is None:
            continue
        elements.append((elem_id, node_i, node_j, section_name))
        area = section_areas[section_name]
        mat_tag = section_to_mat[section_name]
        if is_geom_nonlin:
            ops.element("CorotTruss", elem_id, node_i, node_j, area, mat_tag)
        else:
            ops.element("truss", elem_id, node_i, node_j, area, mat_tag)

    ops.timeSeries("Linear", 1)
    ops.pattern("Plain", 1, 1)
    for load_id, joint_id, magnitude, direction, fx_kn, fy_kn, fz_kn, load_type in load_data:
        # Convert kN -> N using explicit signed components from truss.txt
        ops.load(joint_id, fx_kn * 1000.0, fy_kn * 1000.0, fz_kn * 1000.0)

    ops.system("FullGeneral")
    ops.numberer("Plain")
    ops.constraints("Plain")
    ops.test('NormDispIncr', 1.0e-4, 100, 0)

    if not (is_geom_nonlin or is_mat_nonlin):
        # Linear problem: one load step is exact.
        ops.algorithm("Linear")
        ops.integrator("LoadControl", 1.0)
        ops.analysis("Static")
        ok = ops.analyze(1)
    else:
        # Nonlinear problem: incremental load stepping with Newton-Raphson,
        # falling back to Modified Newton for a step that does not converge.
        n_steps = 10
        ops.algorithm("Newton")
        ops.integrator("LoadControl", 1.0 / n_steps)
        ops.analysis("Static")
        ok = 0
        for _ in range(n_steps):
            ok = ops.analyze(1)
            if ok != 0:
                ops.algorithm("ModifiedNewton", "-initial")
                ok = ops.analyze(1)
                ops.algorithm("Newton")
            if ok != 0:
                break

    joint_displacements = {}
    for joint_id in sorted(joints.keys()):
        dx = ops.nodeDisp(joint_id, 1)
        dy = ops.nodeDisp(joint_id, 2)
        dz = ops.nodeDisp(joint_id, 3)
        joint_displacements[joint_id] = (dx, dy, dz)

    member_forces = {}
    member_stresses = {}
    for elem_id, node_i, node_j, section_name in elements:
        area = section_areas[section_name]
        axial_force = ops.basicForce(elem_id)[0]
        stress = axial_force / area
        member_forces[elem_id] = axial_force
        member_stresses[elem_id] = stress

    return {
        'success': (ok == 0),
        'joint_displacements': joint_displacements,
        'member_forces': member_forces,
        'member_stresses': member_stresses,
        'elements': elements,
    }


# Diagnostics of the last call to run_truss_analysis(); read by the agent tool when a run fails.
LAST_DIAGNOSTICS = None


def validate_truss_model(truss_data, section_areas, tol=1e-6):
    """
    Pre-analysis checks of a parsed truss model (paper, Table 1).

    Returns {'error': [...], 'warning': [...], 'info': [...]} with one message per finding.
    """
    joints = truss_data['joints']
    elements = truss_data['elements']
    bcs = truss_data['boundary_conditions']
    diag = {'error': [], 'warning': [], 'info': []}

    degree = {jid: 0 for jid in joints}
    for elem_id, start, end, section in elements:
        ni = find_joint_by_coords(*start, joints)
        nj = find_joint_by_coords(*end, joints)
        if ni is None or nj is None:
            diag['warning'].append(f"Element {elem_id} skipped: an end point does not match any joint.")
            continue
        degree[ni] += 1
        degree[nj] += 1
        area = section_areas.get(section)
        if area is None or area <= 0:
            diag['error'].append(f"Element {elem_id}: section '{section}' has no valid area.")

    supported = {jid for _, jid, _, _ in bcs}
    for jid, deg in sorted(degree.items()):
        if deg == 0:
            diag['error'].append(f"Joint {jid} is disconnected (no element attached).")
        elif deg == 1 and jid not in supported:
            diag['warning'].append(f"Joint {jid} is dangling (one element, no support): mechanism.")

    if not bcs:
        diag['error'].append("No boundary conditions: the stiffness matrix is singular.")
    n_fixed = sum(len(c) for _, _, _, c in bcs)
    if bcs and n_fixed < 6:
        diag['warning'].append(f"Only {n_fixed} restrained DOFs (< 6): rigid-body motion is possible.")

    ids = sorted(joints)
    for a in range(len(ids)):
        for b in range(a + 1, len(ids)):
            if all(abs(p - q) <= tol for p, q in zip(joints[ids[a]], joints[ids[b]])):
                diag['warning'].append(f"Joints {ids[a]} and {ids[b]} share the same coordinates.")

    m, r, n = sum(1 for e in elements), n_fixed, len(joints)
    if m + r < 3 * n:
        diag['info'].append(f"m + r = {m + r} < 3n = {3 * n}: the truss is statically unstable (mechanism).")
    elif m + r == 3 * n:
        diag['info'].append(f"m + r = 3n = {3 * n}: statically determinate if geometrically stable.")
    else:
        diag['info'].append(f"m + r = {m + r} > 3n = {3 * n}: statically indeterminate "
                            f"to degree {m + r - 3 * n}.")
    return diag


def format_diagnostics(diag):
    """Readable diagnostics with the remediation hints relayed to the LLM."""
    hints = {
        'disconnected': "remove the orphan joint or connect it with a member",
        'dangling': "add a member or a support at the dangling joint",
        'No boundary': "assign supports (pinned/roller blocks) in AutoCAD",
        'restrained': "add supports so that at least 6 DOFs are restrained",
        'skipped': "snap member end points to joint blocks in the drawing",
        'no valid area': "use a layer name from the CHS catalogue (e.g. CHS_114.3x3.6)",
        'same coordinates': "delete the duplicate joint block",
        'unstable': "add members or supports (bracing)",
    }
    lines = []
    for level in ('error', 'warning', 'info'):
        for msg in diag.get(level, []):
            hint = next((h for k, h in hints.items() if k in msg), None)
            lines.append(f"[{level.upper()}] {msg}" + (f" -> Suggestion: {hint}." if hint else ""))
    return "\n".join(lines)


def run_truss_analysis(truss_file_path, output_file_path, is_geom_nonlin=True, is_mat_nonlin=True):
    """
    Run 3D truss analysis with optional geometric and material nonlinearity.

    Parameters:
    -----------
    truss_file_path : str
        Path to the truss.txt file containing geometry data
    output_file_path : str
        Path to the output result.txt file
    is_geom_nonlin : bool
        If True, use CorotTruss for large displacement analysis
    is_mat_nonlin : bool
        If True, use Steel01 material for material nonlinearity

    Returns:
    --------
    joint_displacements : dict
        Displacements at each joint {joint_id: (dx, dy, dz)}
    member_stresses : dict
        Axial stress in each member {elem_id: stress}
    """

    # Read truss data from file
    truss_data = read_truss_file(truss_file_path)
    joints = truss_data['joints']
    element_data = truss_data['elements']
    load_data = truss_data['loads']
    bc_data = truss_data['boundary_conditions']

    # Load section properties from CSV (area and radius of gyration)
    csv_path = os.path.join(os.path.dirname(__file__), "CHS_eurocode.csv")
    all_sections = load_chs_properties(csv_path)

    # Also load full catalog for radius of gyration (i)
    try:
        from truss_optimizer import load_section_catalog, chs_properties_from_area
        full_catalog = load_section_catalog(csv_path)
        # Build name -> full props lookup
        catalog_by_name = {s['ID']: s for s in full_catalog}
    except ImportError:
        full_catalog = []
        catalog_by_name = {}

    sections = {}       # section_name -> area_mm2 (for OpenSees)
    section_i = {}      # section_name -> radius_of_gyration_mm (for utilization)
    section_names = list(set(elem[3] for elem in element_data))
    section_names.sort()
    for name in section_names:
        if name in all_sections:
            sections[name] = all_sections[name]['A']
            print(f"Section '{name}': A = {sections[name]} mm² (from CSV)")
            # Get i from full catalog
            if name in catalog_by_name:
                section_i[name] = catalog_by_name[name]['i']
        else:
            # Parse area from section name (e.g. "A10_5.060" -> 5.060 cm² -> 506.0 mm²)
            parts = name.rsplit('_', 1)
            if len(parts) == 2:
                try:
                    area_cm2 = float(parts[1])
                    sections[name] = area_cm2 * 100.0  # cm² -> mm²
                    print(f"Section '{name}': A = {sections[name]} mm² (from name)")
                except ValueError:
                    print(f"WARNING: Section '{name}' not found in CSV and cannot parse area from name.")
                    continue
            else:
                print(f"WARNING: Section '{name}' not found in CSV and cannot parse area from name.")
                continue

        # Estimate i if not from catalog
        if name not in section_i and name in sections and sections[name] > 0:
            try:
                props = chs_properties_from_area(sections[name])
                section_i[name] = props['i']
            except Exception:
                section_i[name] = 0.0

    # Pre-analysis diagnostics (Table 1 of the paper); errors stop the run before OpenSees is called.
    global LAST_DIAGNOSTICS
    LAST_DIAGNOSTICS = validate_truss_model(truss_data, sections)
    if LAST_DIAGNOSTICS['error']:
        with open(output_file_path, 'w') as f:
            f.write("Analysis Status: FAILED (model errors)\n\n" + format_diagnostics(LAST_DIAGNOSTICS) + "\n")
        return None, None

    # Run analysis using in-memory function
    result = run_analysis_in_memory(truss_data, sections, is_geom_nonlin, is_mat_nonlin)
    analysis_success = result['success']
    joint_displacements = result['joint_displacements']
    member_forces = result['member_forces']
    member_stresses = result['member_stresses']
    elements = result['elements']

    # Write results to file
    with open(output_file_path, 'w') as f:
        f.write("=" * 80 + "\n")
        f.write("3D TRUSS ANALYSIS RESULTS\n")
        f.write("=" * 80 + "\n\n")

        f.write(f"Input file: {truss_file_path}\n")
        f.write(f"Geometric Nonlinearity: {is_geom_nonlin}\n")
        f.write(f"Material Nonlinearity: {is_mat_nonlin}\n")
        f.write(f"Analysis Status: {'SUCCESS' if analysis_success else 'FAILED'}\n\n")

        # Joint coordinates
        f.write("=" * 80 + "\n")
        f.write("JOINT COORDINATES\n")
        f.write("=" * 80 + "\n")
        f.write(f"{'Joint':<8} {'X':<15} {'Y':<15} {'Z':<15}\n")
        f.write("-" * 53 + "\n")
        for joint_id, (x, y, z) in sorted(joints.items()):
            f.write(f"{joint_id:<8} {x:<15.4f} {y:<15.4f} {z:<15.4f}\n")
        f.write("\n")

        # Boundary conditions
        f.write("=" * 80 + "\n")
        f.write("BOUNDARY CONDITIONS\n")
        f.write("=" * 80 + "\n")
        f.write(f"{'Joint':<8} {'Type':<15} {'Fix X':<8} {'Fix Y':<8} {'Fix Z':<8}\n")
        f.write("-" * 47 + "\n")
        for bc_id, joint_id, bc_type, constraints in bc_data:
            fix_x = 1 if 1 in constraints else 0
            fix_y = 1 if 2 in constraints else 0
            fix_z = 1 if 3 in constraints else 0
            f.write(f"{joint_id:<8} {bc_type:<15} {fix_x:<8} {fix_y:<8} {fix_z:<8}\n")
        f.write("\n")

        # Section properties
        f.write("=" * 80 + "\n")
        f.write("SECTION PROPERTIES\n")
        f.write("=" * 80 + "\n")
        f.write(f"{'Section':<20} {'A (mm2)':<15}\n")
        f.write("-" * 35 + "\n")
        for name in section_names:
            if name in sections:
                f.write(f"{name:<20} {sections[name]:<15.2f}\n")
        f.write("\n")

        # Element connectivity
        f.write("=" * 80 + "\n")
        f.write("ELEMENT CONNECTIVITY\n")
        f.write("=" * 80 + "\n")
        f.write(f"{'Elem':<8} {'Node i':<10} {'Node j':<10} {'Section':<20}\n")
        f.write("-" * 48 + "\n")
        for elem_id, node_i, node_j, section_name in elements:
            f.write(f"{elem_id:<8} {node_i:<10} {node_j:<10} {section_name:<20}\n")
        f.write("\n")

        # Applied loads
        f.write("=" * 80 + "\n")
        f.write("APPLIED LOADS\n")
        f.write("=" * 80 + "\n")
        f.write(f"{'Load':<6} {'Joint':<8} {'Mag (kN)':<12} {'Dir':<6} {'Fx (N)':<15} {'Fy (N)':<15} {'Fz (N)':<15}\n")
        f.write("-" * 80 + "\n")
        for load_id, joint_id, magnitude, direction, fx_kn, fy_kn, fz_kn, load_type in load_data:
            f.write(f"{load_id:<6} {joint_id:<8} {magnitude:<12.2f} {direction:<6} {fx_kn*1000:<15.2f} {fy_kn*1000:<15.2f} {fz_kn*1000:<15.2f}\n")
        f.write("\n")

        # Joint displacements
        f.write("=" * 80 + "\n")
        f.write("JOINT DISPLACEMENTS\n")
        f.write("=" * 80 + "\n")
        f.write(f"{'Joint':<8} {'dx (mm)':<18} {'dy (mm)':<18} {'dz (mm)':<18}\n")
        f.write("-" * 62 + "\n")
        for joint_id, (dx, dy, dz) in sorted(joint_displacements.items()):
            f.write(f"{joint_id:<8} {dx:<18.6f} {dy:<18.6f} {dz:<18.6f}\n")
        f.write("\n")

        # Member forces and stresses
        f.write("=" * 80 + "\n")
        f.write("MEMBER AXIAL FORCES AND STRESSES\n")
        f.write("=" * 80 + "\n")
        f.write(f"{'Elem':<8} {'Section':<20} {'Area (mm2)':<15} {'Force (N)':<18} {'Stress (MPa)':<15}\n")
        f.write("-" * 76 + "\n")
        for elem_id, node_i, node_j, section_name in elements:
            area = sections[section_name]
            force = member_forces[elem_id]
            stress = member_stresses[elem_id]
            f.write(f"{elem_id:<8} {section_name:<20} {area:<15.2f} {force:<18.2f} {stress:<15.4f}\n")
        f.write("\n")
        f.write("Note: Positive stress = Tension, Negative stress = Compression\n\n")

        # EC3 Utilization factors
        try:
            from truss_optimizer import check_tension_unity, check_buckling_unity
            f.write("=" * 80 + "\n")
            f.write("EC3 UTILIZATION FACTORS\n")
            f.write("=" * 80 + "\n")
            f.write(f"{'Elem':<8} {'Section':<20} {'Force (N)':<15} {'Type':<12} {'Utilization':<12}\n")
            f.write("-" * 67 + "\n")
            for elem_id, node_i, node_j, section_name in elements:
                area = sections.get(section_name, 0)
                force = member_forces.get(elem_id, 0)
                i_val = section_i.get(section_name, 0)

                # Compute element length
                ni = joints.get(node_i, (0, 0, 0))
                nj = joints.get(node_j, (0, 0, 0))
                L_mm = math.sqrt(sum((a - b)**2 for a, b in zip(ni, nj))) * 1000.0

                if force > 0:
                    unity = check_tension_unity(force, area) if area > 0 else 0.0
                    check_type = "Tension"
                elif force < 0:
                    unity = check_buckling_unity(force, area, L_mm, i_val) if area > 0 and i_val > 0 else 0.0
                    check_type = "Buckling"
                else:
                    unity = 0.0
                    check_type = "None"

                f.write(f"{elem_id:<8} {section_name:<20} {force:<15.2f} {check_type:<12} {unity:<12.4f}\n")
            f.write("\n")
        except ImportError:
            pass  # Skip utilization if optimizer not available

        # Summary
        f.write("=" * 80 + "\n")
        f.write("ANALYSIS SUMMARY\n")
        f.write("=" * 80 + "\n")

        max_disp = 0
        max_disp_joint = 0
        for joint_id, (dx, dy, dz) in joint_displacements.items():
            total_disp = math.sqrt(dx**2 + dy**2 + dz**2)
            if total_disp > max_disp:
                max_disp = total_disp
                max_disp_joint = joint_id

        f.write(f"Maximum total displacement: {max_disp:.6f} mm at Joint {max_disp_joint}\n")

        max_stress = 0
        max_stress_elem = 0
        for elem_id, stress in member_stresses.items():
            if abs(stress) > abs(max_stress):
                max_stress = stress
                max_stress_elem = elem_id

        stress_type = "Tension" if max_stress > 0 else "Compression"
        f.write(f"Maximum axial stress: {abs(max_stress):.6f} MPa ({stress_type}) in Element {max_stress_elem}\n")
        f.write("=" * 80 + "\n")
        if not analysis_success:
            f.write("\nOpenSees did not converge. Pre-analysis diagnostics:\n")
            f.write(format_diagnostics(LAST_DIAGNOSTICS) + "\n")

    if not analysis_success:
        return None, None
    return joint_displacements, member_stresses


def main():
    """Main function to run truss analysis from file"""

    truss_file = os.path.join(os.path.dirname(__file__), "truss.txt")
    output_file = os.path.join(os.path.dirname(__file__), "result.txt")

    if not os.path.exists(truss_file):
        return None, None

    displacements, stresses = run_truss_analysis(
        truss_file,
        output_file,
        is_geom_nonlin=True,
        is_mat_nonlin=True
    )

    return displacements, stresses


if __name__ == "__main__":
    displacements, stresses = main()
