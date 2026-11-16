"""
Headless reproduction of the BIM-ATA analysis and optimization agents (no AutoCAD, no LLM needed).

    python run_example.py examples/demo_12_joint/truss.txt                 # linear analysis
    python run_example.py examples/demo_12_joint/truss.txt --nonlinear     # geometric + material nonlinearity
    python run_example.py examples/demo_12_joint/truss.txt --optimize PSO  # + Eurocode 3 mass optimization

Outputs (result.txt, truss_optimized.txt, result_optimized.txt) are written next to the input file.
"""
import argparse
import os

import truss3D_analysis_from_file as fa
from truss_optimizer import optimize_truss


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("truss_file")
    ap.add_argument("--nonlinear", action="store_true")
    ap.add_argument("--optimize", choices=["PSO", "GA", "DE", "ALL"])
    ap.add_argument("--pop", type=int, default=50)
    ap.add_argument("--gen", type=int, default=100)
    a = ap.parse_args()

    folder = os.path.dirname(os.path.abspath(a.truss_file))
    result = os.path.join(folder, "result.txt")
    d, s = fa.run_truss_analysis(a.truss_file, result, a.nonlinear, a.nonlinear)
    if d is None:
        print("Analysis failed:\n" + fa.format_diagnostics(fa.LAST_DIAGNOSTICS))
        return
    print(open(result).read().split("ANALYSIS SUMMARY")[-1])

    if a.optimize:
        out = optimize_truss(truss_file=a.truss_file, algorithm=a.optimize, pop_size=a.pop, n_gen=a.gen,
                             is_geom_nonlin=a.nonlinear, is_mat_nonlin=a.nonlinear)
        print(out.get("report", out) if isinstance(out, dict) else out)
        opt = os.path.join(folder, "truss_optimized.txt")
        if os.path.exists(opt):
            fa.run_truss_analysis(opt, os.path.join(folder, "result_optimized.txt"), a.nonlinear, a.nonlinear)


if __name__ == "__main__":
    main()
