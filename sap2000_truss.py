"""
SAP2000 OAPI - 3D Truss Numerical Model
========================================
Reads truss geometry, sections, loads, and BCs from:
  - truss.txt          (joints, elements, loads, boundary conditions)
  - CHS_eurocode.csv   (section properties — area used for A-series sections)

Builds and analyses the model in SAP2000 via the Open API (comtypes).

Units throughout: kN, m, °C
  - Coordinates  : metres  (as in truss.txt)
  - Cross-section: m²      (converted from cm² in CSV)
  - Force        : kN
  - Elastic mod  : kN/m²   (210 GPa = 2.1e8 kN/m²)

Requirements:
  pip install comtypes
  SAP2000 v20+ installed and COM-registered
"""

import sys
import os
import math
import csv

# ── File paths ────────────────────────────────────────────────────────────────
BASE_DIR    = os.path.dirname(os.path.abspath(__file__))
TRUSS_TXT   = os.path.join(BASE_DIR, "truss.txt")
CHS_CSV     = os.path.join(BASE_DIR, "CHS_eurocode.csv")
MODEL_PATH  = os.path.join(BASE_DIR, "truss_sap2000.sdb")
RESULTS_TXT = os.path.join(BASE_DIR, "sap2000_results.txt")

# ── SAP2000 connection ────────────────────────────────────────────────────────
SAP2000_PROG_ID = "CSI.SAP2000.API.SapObject"

# ── Unit system enum (SAP2000) ────────────────────────────────────────────────
UNITS_KN_M_C = 6   # kN, metre, Celsius  (9 = N·mm — wrong!)

# ── Material: structural steel S355 ──────────────────────────────────────────
MAT_NAME  = "S355"
E_KN_M2   = 2.1e8      # Young's modulus  [kN/m²]  (210 GPa)
NU        = 0.3         # Poisson's ratio
ALPHA     = 1.17e-5     # Thermal expansion coefficient [1/°C]

# ── Load pattern name ─────────────────────────────────────────────────────────
LOAD_PATTERN = "LIVE"


# ═══════════════════════════════════════════════════════════════════════════════
# 1.  PARSERS
# ═══════════════════════════════════════════════════════════════════════════════

def parse_truss_txt(filepath):
    """
    Parse truss.txt.

    Returns
    -------
    joints   : dict  {id (int): (x, y, z)}                          metres
    elements : dict  {id (int): (xi,yi,zi, xj,yj,zj, L, sec_name)}
    loads    : list  [(joint_id, Fx, Fy, Fz)]                        kN
    bcs      : list  [(joint_id, [dof_indices])]  1=UX, 2=UY, 3=UZ
    """
    joints   = {}
    elements = {}
    loads    = []
    bcs      = []
    section  = None   # current data block

    SKIP_STARTS = ("===", "---", "Node ID", "Elem", "Load ", "BC ",
                   "DRAWING", "layer_color", "joint_block", "(")

    with open(filepath) as fh:
        for raw in fh:
            line = raw.strip()

            # ── block header detection ──────────────────────────────────────
            if "TRUSS JOINT COORDINATES"  in line: section = "JOINTS";   continue
            if "TRUSS ELEMENT INFORMATION" in line: section = "ELEMENTS"; continue
            if "APPLIED LOADS"             in line: section = "LOADS";    continue
            if "BOUNDARY CONDITIONS"       in line: section = "BCS";      continue

            # ── skip non-data lines ─────────────────────────────────────────
            if not line or any(line.startswith(s) for s in SKIP_STARTS):
                continue

            parts = line.split()
            if not parts:
                continue

            # ── joints ─────────────────────────────────────────────────────
            if section == "JOINTS" and len(parts) >= 4:
                try:
                    jid = int(parts[0])
                    joints[jid] = (float(parts[1]), float(parts[2]), float(parts[3]))
                except ValueError:
                    pass

            # ── elements ───────────────────────────────────────────────────
            elif section == "ELEMENTS" and len(parts) >= 9:
                try:
                    eid = int(parts[0])
                    xi, yi, zi = float(parts[1]), float(parts[2]), float(parts[3])
                    xj, yj, zj = float(parts[4]), float(parts[5]), float(parts[6])
                    length      = float(parts[7])
                    sec_name    = parts[8]
                    elements[eid] = (xi, yi, zi, xj, yj, zj, length, sec_name)
                except (ValueError, IndexError):
                    pass

            # ── loads ──────────────────────────────────────────────────────
            # columns: ID  Type  Value  JointID  Dir  Fx  Fy  Fz  X  Y  Z
            elif section == "LOADS" and len(parts) >= 8:
                try:
                    joint_id = int(parts[3])
                    fx = float(parts[5])   # kN, signed
                    fy = float(parts[6])   # kN, signed
                    fz = float(parts[7])   # kN, signed
                    loads.append((joint_id, fx, fy, fz))
                except (ValueError, IndexError):
                    pass

            # ── boundary conditions ────────────────────────────────────────
            # columns: BC  Type  X  Y  Z  JointID  Constraints
            elif section == "BCS" and len(parts) >= 7:
                try:
                    joint_id = int(parts[5])
                    constrs  = [int(c) for c in parts[6].split(",")]
                    bcs.append((joint_id, constrs))
                except (ValueError, IndexError):
                    pass

    return joints, elements, loads, bcs


def parse_sections_csv(filepath):
    """
    Parse CHS_eurocode.csv.

    Returns dict: section_name -> cross-sectional area [cm²]
    Column order: ID, d, t, mass, A, d/t, I, i, Wel, Wpl, IT, Wt
    The A-series rows (e.g. A1_9.032) have only the area field filled.
    """
    sections = {}
    with open(filepath) as fh:
        reader = csv.reader(fh)
        next(reader)   # column-name header
        next(reader)   # units header
        for row in reader:
            if len(row) >= 5 and row[0].strip():
                name     = row[0].strip()
                area_str = row[4].strip()
                if area_str:
                    try:
                        sections[name] = float(area_str)
                    except ValueError:
                        pass
    return sections


def find_joint_id(joints, x, y, z, tol=1e-3):
    """Return the joint ID whose coordinates match (x, y, z) within tol."""
    for jid, (jx, jy, jz) in joints.items():
        if abs(jx - x) < tol and abs(jy - y) < tol and abs(jz - z) < tol:
            return jid
    return None


# ═══════════════════════════════════════════════════════════════════════════════
# 2.  SAP2000 MODEL BUILDER
# ═══════════════════════════════════════════════════════════════════════════════

def build_model():
    sep = "=" * 65
    print(sep)
    print("  SAP2000 OAPI  –  3D Truss Numerical Model Builder")
    print(sep)

    # ── 2.1  Parse input files ────────────────────────────────────────────────
    print("\n[Step 1/8]  Parsing input files …")
    joints, elements, loads, bcs = parse_truss_txt(TRUSS_TXT)
    sec_db = parse_sections_csv(CHS_CSV)

    print(f"  Joints   : {len(joints)}")
    print(f"  Elements : {len(elements)}")
    print(f"  Loads    : {len(loads)}")
    print(f"  BCs      : {len(bcs)}")
    print(f"  Sections in CSV : {len(sec_db)}")

    # Resolve start/end joint IDs for every element
    elem_joints = {}
    for eid, (xi, yi, zi, xj, yj, zj, *_) in elements.items():
        ji = find_joint_id(joints, xi, yi, zi)
        jj = find_joint_id(joints, xj, yj, zj)
        if ji is None or jj is None:
            raise ValueError(
                f"Element {eid}: cannot match endpoint coordinates to any joint.\n"
                f"  Start: ({xi}, {yi}, {zi})\n"
                f"  End  : ({xj}, {yj}, {zj})"
            )
        elem_joints[eid] = (ji, jj)

    # ── 2.2  Connect to SAP2000 ───────────────────────────────────────────────
    print("\n[Step 2/8]  Connecting to SAP2000 …")
    import comtypes.client
    try:
        # Attach to an already-running SAP2000 instance (preferred)
        sap_obj = comtypes.client.GetActiveObject(SAP2000_PROG_ID)
        sap = sap_obj.SapModel
        print("  Attached to running SAP2000 instance.")
    except OSError:
        # No running instance found — launch a new one
        try:
            sap_obj = comtypes.client.CreateObject(SAP2000_PROG_ID)
            sap_obj.ApplicationStart()
            sap = sap_obj.SapModel
            print("  Started new SAP2000 instance.")
        except Exception as exc:
            print(f"\n  ERROR: Could not connect to SAP2000.\n  {exc}")
            print("  Ensure SAP2000 is installed and its COM server is registered.")
            sys.exit(1)

    # ── 2.2b  Immediately discard any previously loaded model ─────────────────
    # This ensures stale geometry and analysis results from an earlier run are
    # fully cleared before the new model is built.
    print("  Clearing existing model …")
    sap.File.NewBlank()

    # ── 2.3  Initialise blank model with correct units ────────────────────────
    print("\n[Step 3/8]  Initialising model (units: kN · m · °C) …")
    sap.InitializeNewModel(UNITS_KN_M_C)
    sap.File.NewBlank()

    # ── 2.4  Material ─────────────────────────────────────────────────────────
    print("\n[Step 4/8]  Defining material …")
    # eMatType_Steel = 1
    sap.PropMaterial.SetMaterial(MAT_NAME, 1)
    sap.PropMaterial.SetMPIsotropic(MAT_NAME, E_KN_M2, NU, ALPHA)
    print(f"  '{MAT_NAME}'  E = {E_KN_M2:.3e} kN/m²  ν = {NU}  α = {ALPHA} /°C")

    # ── 2.5  Frame sections ───────────────────────────────────────────────────
    print("\n[Step 5/8]  Defining frame sections …")
    unique_secs = sorted(set(e[7] for e in elements.values()))

    for sec_name in unique_secs:
        if sec_name in sec_db:
            area_cm2 = sec_db[sec_name]
        else:
            print(f"  WARNING: '{sec_name}' not found in CSV  →  defaulting to 1.0 cm²")
            area_cm2 = 1.0

        area_m2 = area_cm2 * 1.0e-4          # cm²  →  m²

        # Bending/torsion properties are irrelevant for a truss (moments are
        # released at both ends), but SAP2000's SetGeneral requires non-zero I.
        # Derive from the equivalent solid-circular cross-section.
        r_m   = math.sqrt(area_m2 / math.pi)   # equivalent radius [m]
        I_m4  = math.pi * r_m**4 / 4.0          # I = π r⁴ / 4  [m⁴]
        J_m4  = 2.0 * I_m4                       # J = 2 I  (solid circle)
        As_m2 = 0.9 * area_m2                    # shear area ≈ 0.9 A  [m²]
        d_m   = 2.0 * r_m                        # diameter (used as t3, t2)
        # Section moduli and radius of gyration (equivalent solid circle)
        S_m3  = I_m4 / r_m                       # S = I / c  [m³]
        Z_m3  = (4.0 / 3.0) * r_m**3            # Z_plastic  [m³]
        Rg_m  = r_m / 2.0                        # r_gyration = r/2  [m]

        ret = sap.PropFrame.SetGeneral(
            sec_name,   # section name
            MAT_NAME,   # material
            d_m,        # t3  depth  (local-3 direction)
            d_m,        # t2  width  (local-2 direction)
            area_m2,    # A   cross-sectional area  [m²]
            As_m2,      # As2 shear area in local-2  [m²]
            As_m2,      # As3 shear area in local-3  [m²]
            J_m4,       # J   torsional constant     [m⁴]
            I_m4,       # I22 moment of inertia       [m⁴]
            I_m4,       # I33 moment of inertia       [m⁴]
            S_m3,       # S22 elastic section modulus [m³]
            S_m3,       # S33
            Z_m3,       # Z22 plastic section modulus [m³]
            Z_m3,       # Z33
            Rg_m,       # R22 radius of gyration      [m]
            Rg_m,       # R33
        )
        if ret != 0:
            print(f"  WARNING: SetGeneral returned {ret} for '{sec_name}'")
        else:
            print(f"  {sec_name:<18}  A = {area_cm2:7.3f} cm²  ({area_m2:.6e} m²)")

    # ── 2.6  Joints ───────────────────────────────────────────────────────────
    print("\n[Step 6/8]  Adding joints …")
    # AddCartesian returns a tuple; ret[0] is the actual name SAP2000 assigned.
    jname = {}
    for jid, (x, y, z) in sorted(joints.items()):
        ret = sap.PointObj.AddCartesian(x, y, z, f"J{jid}")
        jname[jid] = ret[0]
        print(f"  J{jid:<3} → '{ret[0]}'  ({x:8.4f}, {y:8.4f}, {z:8.4f})  m")

    # ── Coordinate-based remapping ────────────────────────────────────────────
    # SAP2000 may renumber joints internally (ret[0] is not always "J{jid}").
    # Query every point SAP2000 knows about, build coord→name dict, then
    # remap jname so every jid always refers to the correct physical node.
    print("  Re-mapping joints by coordinates …")
    nN = 0; sap_names = []
    nN, sap_names, _ = sap.PointObj.GetNameList(nN, sap_names)

    coord_to_sapname = {}                          # (rx, ry, rz) → SAP2000 name
    for sn in sap_names:
        sx, sy, sz, _ = sap.PointObj.GetCoordCartesian(sn, 0.0, 0.0, 0.0, "Global")
        key = (round(sx, 4), round(sy, 4), round(sz, 4))
        coord_to_sapname[key] = sn

    for jid, (x, y, z) in joints.items():
        key = (round(x, 4), round(y, 4), round(z, 4))
        matched = coord_to_sapname.get(key)
        if matched is None:
            print(f"  WARNING: joint {jid} at ({x},{y},{z}) not found in SAP2000")
        elif matched != jname[jid]:
            print(f"  Remapping J{jid}: '{jname[jid]}' → '{matched}'")
            jname[jid] = matched

    # ── 2.7  Frame elements with truss releases ───────────────────────────────
    print("\n[Step 7/8]  Adding frame elements (pin-pin releases) …")

    # Truss release flags — SAP2000 DOF order: [P, V2, V3, T, M2, M3]
    # Release moments M2 (index 4) and M3 (index 5) at both I and J ends.
    ii_rel = [False, False, False, False, True, True]   # start-end releases
    ij_rel = [False, False, False, False, True, True]   # far-end releases
    sv     = [0.0, 0.0, 0.0, 0.0, 0.0, 0.0]            # spring stiffness (0 = free)
    ev     = [0.0, 0.0, 0.0, 0.0, 0.0, 0.0]

    # AddByPoint returns a tuple; ret[0] is the actual name SAP2000 assigned.
    fname = {}
    for eid, (xi, yi, zi, xj, yj, zj, length, sec_name) in sorted(elements.items()):
        ji, jj = elem_joints[eid]
        ni, nj = jname[ji], jname[jj]

        ret = sap.FrameObj.AddByPoint(ni, nj, f"E{eid}", sec_name)
        fname[eid] = ret[0]

        sap.FrameObj.SetReleases(ret[0], ii_rel, ij_rel, sv, ev)

        print(f"  E{eid:<3} → '{ret[0]}'  {ni}→{nj}  sec={sec_name:<18}  L={length:.4f} m")

    # ── 2.8  Boundary conditions ──────────────────────────────────────────────
    print("\n[Step 8a/8]  Applying boundary conditions …")
    # 3dpin_solid: restrain UX(1), UY(2), UZ(3) — free rotations
    for joint_id, constrs in bcs:
        # Restraint array: [UX, UY, UZ, RX, RY, RZ]
        restraint = [False, False, False, False, False, False]
        for c in constrs:
            if c == 1: restraint[0] = True   # UX
            if c == 2: restraint[1] = True   # UY
            if c == 3: restraint[2] = True   # UZ
        sap.PointObj.SetRestraint(jname[joint_id], restraint)
        active = [("UX","UY","UZ")[i] for i in range(3) if restraint[i]]
        print(f"  {jname[joint_id]}  →  fixed: {', '.join(active)}")

    # ── 2.9  Load pattern & joint forces ─────────────────────────────────────
    print("\n[Step 8b/8]  Defining load pattern and applying loads …")

    # eLoadPatternType: 1=Dead, 2=SuperDead, 3=Live  ; addAnalysisCase=True
    sap.LoadPatterns.Add(LOAD_PATTERN, 3, 0.0, True)

    for joint_id, fx, fy, fz in loads:
        # SetLoadForce(Name, LoadPat, Value[6], Replace, CSys, ItemType)
        # Value = [F1(X), F2(Y), F3(Z), M1, M2, M3]
        force = [fx, fy, fz, 0.0, 0.0, 0.0]
        sap.PointObj.SetLoadForce(jname[joint_id], LOAD_PATTERN,
                                  force, False, "Global", 0)
        print(f"  {jname[joint_id]}  →  F = ({fx:.1f}, {fy:.1f}, {fz:.1f}) kN")

    # Refresh SAP2000 display so joints and frames become visible
    sap.View.RefreshView(0, False)

    # ── Save before analysis ──────────────────────────────────────────────────
    print(f"\nSaving model  →  {MODEL_PATH}")
    sap.File.Save(MODEL_PATH)

    # ── Run analysis ──────────────────────────────────────────────────────────
    # Delete any results from a previous run so the new analysis starts clean.
    print("Deleting previous analysis results …")
    sap.Analyze.DeleteResults("", True)

    print("Running analysis …")
    sap.Analyze.SetRunCaseFlag("", False, True)              # disable ALL cases
    sap.Analyze.SetRunCaseFlag(LOAD_PATTERN, True, False)    # enable LIVE only
    ret = sap.Analyze.RunAnalysis()
    if ret != 0:
        print(f"  ERROR: RunAnalysis() returned code {ret}")
        return
    print("  Analysis completed successfully.")

    # ═══════════════════════════════════════════════════════════════════════════
    # 3.  EXTRACT & REPORT RESULTS
    # ═══════════════════════════════════════════════════════════════════════════
    sap.Results.Setup.DeselectAllCasesAndCombosForOutput()
    sap.Results.Setup.SetCaseSelectedForOutput(LOAD_PATTERN)

    out_lines = []

    def out(text=""):
        print(text)
        out_lines.append(text)

    out()
    out(sep)
    out("  ANALYSIS RESULTS")
    out(f"  Load pattern : {LOAD_PATTERN}  ({len(loads)} load(s) applied)")
    out(f"  Units        : kN, m")
    out(sep)

    # ── 3.1  Joint displacements ──────────────────────────────────────────────
    out()
    out("JOINT DISPLACEMENTS")
    hdr = f"  {'Joint':<8}{'UX (mm)':>14}{'UY (mm)':>14}{'UZ (mm)':>14}"
    out(hdr)
    out("  " + "-" * (len(hdr) - 2))

    for jid in sorted(joints.keys()):
        nR = 0
        obj=[]; elm=[]; lc=[]; st=[]; sn=[]
        u1=[]; u2=[]; u3=[]; r1=[]; r2=[]; r3=[]

        nR, obj, elm, lc, st, sn, u1, u2, u3, r1, r2, r3, ret = \
            sap.Results.JointDispl(
                jname[jid], 0, nR,
                obj, elm, lc, st, sn,
                u1, u2, u3, r1, r2, r3)

        if u1:   # non-empty array → results available (m → mm)
            out(f"  {jname[jid]:<8} {u1[0]*1000:>13.6f} {u2[0]*1000:>13.6f} {u3[0]*1000:>13.6f}")
        else:
            out(f"  {jname[jid]:<8} {'N/A':>13} {'N/A':>13} {'N/A':>13}  (no result)")

    # ── 3.2  Frame axial forces ───────────────────────────────────────────────
    out()
    out("FRAME MEMBER AXIAL FORCES")
    out(f"  (T = Tension, C = Compression)")
    hdr2 = f"  {'Elem':<7}{'Section':<20}{'Ji':>4}{'Jj':>4}{'L (m)':>10}{'Axial (kN)':>14}  Type"
    out(hdr2)
    out("  " + "-" * (len(hdr2) - 2))

    for eid in sorted(elements.keys()):
        xi, yi, zi, xj, yj, zj, length, sec_name = elements[eid]
        ji, jj  = elem_joints[eid]

        nR = 0
        obj=[]; osta=[]; elm=[]; esta=[]
        lc=[]; st=[]; sn=[]
        P=[]; V2=[]; V3=[]; T=[]; M2=[]; M3=[]

        nR, obj, osta, elm, esta, lc, st, sn, P, V2, V3, T, M2, M3, ret = \
            sap.Results.FrameForce(
                fname[eid], 0, nR,
                obj, osta, elm, esta,
                lc, st, sn, P, V2, V3, T, M2, M3)

        if P:    # non-empty array → results available
            axial = P[0]   # positive = tension in SAP2000
            typ   = "T" if axial >= 0 else "C"
            out(f"  {fname[eid]:<8} {sec_name:<20} {jname[ji]:>5} {jname[jj]:>5} "
                f"{length:>9.4f} {axial:>13.3f}  {typ}")
        else:
            out(f"  {fname[eid]:<8} {sec_name:<20} {jname[ji]:>5} {jname[jj]:>5} "
                f"{length:>9.4f} {'N/A':>13}")

    # ── 3.3  Support reactions ────────────────────────────────────────────────
    out()
    out("SUPPORT REACTIONS")
    hdr3 = f"  {'Joint':<8}{'Rx (kN)':>14}{'Ry (kN)':>14}{'Rz (kN)':>14}"
    out(hdr3)
    out("  " + "-" * (len(hdr3) - 2))

    supported = [bc[0] for bc in bcs]
    for jid in sorted(supported):
        nR = 0
        obj=[]; elm=[]; lc=[]; st=[]; sn=[]
        f1=[]; f2=[]; f3=[]; m1=[]; m2=[]; m3=[]

        nR, obj, elm, lc, st, sn, f1, f2, f3, m1, m2, m3, ret = \
            sap.Results.JointReact(
                jname[jid], 0, nR,
                obj, elm, lc, st, sn,
                f1, f2, f3, m1, m2, m3)

        if f1:   # non-empty array → results available
            out(f"  {jname[jid]:<8} {f1[0]:>13.3f} {f2[0]:>13.3f} {f3[0]:>13.3f}")
        else:
            out(f"  {jname[jid]:<8} {'N/A':>13} {'N/A':>13} {'N/A':>13}  (no result)")

    # ── 3.4  Save results text file ───────────────────────────────────────────
    with open(RESULTS_TXT, "w") as fh:
        fh.write("\n".join(out_lines))

    out()
    out(f"Results saved  →  {RESULTS_TXT}")
    out(f"Model saved    →  {MODEL_PATH}")
    out("Done.")


# ═══════════════════════════════════════════════════════════════════════════════
if __name__ == "__main__":
    build_model()
