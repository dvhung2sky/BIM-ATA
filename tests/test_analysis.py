"""Regression and diagnostics tests. Run from the repository root:  python -m pytest tests"""
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import truss3D_analysis_from_file as fa  # noqa: E402

DEMO = os.path.join(ROOT, "examples", "demo_12_joint")


def _summary(path):
    txt = open(path).read()
    disp = float(re.search(r"Maximum total displacement: ([\d.]+)", txt).group(1))
    stress = float(re.search(r"Maximum axial stress: ([\d.]+)", txt).group(1))
    return disp, stress


def test_linear_reproduces_reference(tmp_path):
    """Linear OpenSees run reproduces the published result file of the demo truss."""
    out = tmp_path / "result.txt"
    d, s = fa.run_truss_analysis(os.path.join(DEMO, "truss.txt"), str(out), False, False)
    assert d and s
    ref_d, ref_s = _summary(os.path.join(DEMO, "result.txt"))
    got_d, got_s = _summary(out)
    assert abs(got_d - ref_d) < 1e-6 and abs(got_s - ref_s) < 1e-6


def test_linear_matches_sap2000():
    """Nodal displacements agree with the SAP2000 OAPI run of the same model (examples/.../sap2000_results.txt)."""
    data = fa.read_truss_file(os.path.join(DEMO, "truss.txt"))
    sap = {}
    txt = open(os.path.join(DEMO, "sap2000_results.txt")).read()
    block = txt.split("JOINT DISPLACEMENTS")[1].split("FRAME MEMBER")[0]
    for line in block.splitlines():
        p = line.split()
        if len(p) == 4 and p[0].isdigit():
            sap[int(p[0])] = tuple(map(float, p[1:]))
    areas = fa.load_chs_properties(os.path.join(ROOT, "CHS_eurocode.csv"))
    res = fa.run_analysis_in_memory(data, {k: v["A"] for k, v in areas.items()}, False, False)
    assert res["success"]
    for jid, (sx, sy, sz) in sap.items():
        ox, oy, oz = res["joint_displacements"][jid]
        assert abs(ox - sx) < 1e-3 and abs(oy - sy) < 1e-3 and abs(oz - sz) < 1e-3, jid


def test_nonlinear_converges(tmp_path):
    out = tmp_path / "result_nl.txt"
    d, s = fa.run_truss_analysis(os.path.join(DEMO, "truss.txt"), str(out), True, True)
    assert d and s
    lin_d, _ = _summary(os.path.join(DEMO, "result.txt"))
    nl_d, _ = _summary(out)
    assert abs(nl_d - lin_d) / lin_d < 0.05  # small displacements: close to the linear solution


def test_diagnostics_flag_broken_model():
    data = fa.read_truss_file(os.path.join(DEMO, "truss.txt"))
    areas = {e[3]: 100.0 for e in data["elements"]}
    assert not fa.validate_truss_model(data, areas)["error"]

    broken = dict(data, boundary_conditions=[])
    broken["joints"] = {**data["joints"], 999: (50.0, 50.0, 50.0)}
    diag = fa.validate_truss_model(broken, areas)
    msgs = " ".join(diag["error"])
    assert "No boundary conditions" in msgs and "Joint 999 is disconnected" in msgs
    assert "Suggestion" in fa.format_diagnostics(diag)


BENCH = os.path.join(ROOT, "examples", "benchmarks")


def test_benchmarks_reproduce_stored_results(tmp_path):
    """Each benchmark model reproduces its stored linear result; Example 1 also matches Fig. 8 of the paper."""
    names = sorted(n for n in os.listdir(BENCH) if os.path.isdir(os.path.join(BENCH, n)))
    assert len(names) == 5
    for name in names:
        folder = os.path.join(BENCH, name)
        out = tmp_path / f"{name}.txt"
        d, s = fa.run_truss_analysis(os.path.join(folder, "truss.txt"), str(out), False, False)
        assert d and s, name
        ref = _summary(os.path.join(folder, "result.txt"))
        got = _summary(out)
        assert abs(got[0] - ref[0]) < 1e-6 and abs(got[1] - ref[1]) < 1e-6, name
    dome = _summary(os.path.join(BENCH, "ex1_dome_120", "result.txt"))
    assert abs(dome[0] - 168.119167) < 1e-6 and abs(dome[1] - 1014.897) < 1e-3
