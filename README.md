# BIM-ATA

**B**uilding **I**nformation **M**odeling-integrated **A**gentic system for **T**russ **A**nalysis: a multi-agent
large language model (LLM) system that takes a spatial steel truss from an AutoCAD drawing to an analysed,
Eurocode 3-checked and mass-optimized design through natural language requests.

This repository contains the implementation used in the paper

> V.-H. Dang, T.-D. Tran, B.-D. Nguyen. *BIM-ATA: A Multi-Agent Large Language Model System for Code-Compliant
> Analysis and Mass Optimization of Spatial Steel Trusses* (under review).

It extends the conference prototype of
[Dang, Do & Nguyen (2026)](https://doi.org/10.7771/3067-4883.2198), which covered extraction, analysis and plotting
only.

## Architecture

```
truss_chatbot.py  (CustomTkinter GUI: chat, quick-action buttons, plots, 3D viewer)
      |
truss_agent.py    (LangChain + DeepSeek-Chat, ReAct tool calling, conversation memory, system prompt)
      |
truss_tools.py    (9 LangChain tools; schemas also exported for MCP in mcp_truss_tools.py)
      |-- get_truss_joints.py          extraction agent  : AutoCAD (COM) -> truss.txt
      |-- draw_truss_autocad.py        reverse operation : truss.txt / truss_optimized.txt -> AutoCAD
      |-- truss3D_analysis_from_file.py analysis agent   : OpenSeesPy, linear / nonlinear, pre-analysis diagnostics
      |-- rag_indexer.py, rag_retriever.py  EC3 agent    : Marker Markdown -> Qwen3-Embedding-0.6B -> FAISS
      |-- truss_optimizer.py           optimization agent: pymoo PSO / GA / DE, EC3 constraints, CHS catalogue
      |-- truss_viewer_3d.py (+ truss_viewer_window.py)  : Plotly BIM-style viewer (utilization, cost)
sap2000_truss.py  independent check of the same model through the SAP2000 OAPI
```

| File | Content |
|---|---|
| `CHS_eurocode.csv` | Properties of circular hollow sections (EN 10210), used by the analysis agent |
| `CHS_section.csv` | Section catalogue offered to the optimizer |
| `examples/demo_12_joint/` | 12-joint, 31-member truss: model (`truss.txt`), OpenSees results, SAP2000 results, optimized model and its results |
| `examples/autocad/AutoCad_OpenSees_3D_truss.dwg` | AutoCAD drawing used in the study (joint, load and support blocks; sections on `CHS_...` layers) |
| `create_chs_layers.py` | Creates one AutoCAD layer per CHS section, to prepare a new drawing |
| `run_example.py` | Headless analysis + optimization, no AutoCAD or LLM required |
| `tests/` | Regression tests (reference results, SAP2000 agreement, nonlinear convergence, diagnostics) |

## Installation

Python 3.10-3.12 on Windows (AutoCAD and SAP2000 are reached through COM). The analysis, optimization and
tests also run on Linux/macOS.

```bash
pip install -r requirements.txt
```

## Reproduce without AutoCAD or an LLM

```bash
python run_example.py examples/demo_12_joint/truss.txt                  # linear OpenSees analysis
python run_example.py examples/demo_12_joint/truss.txt --nonlinear      # CorotTruss + Steel01, Newton-Raphson
python run_example.py examples/demo_12_joint/truss.txt --optimize ALL   # PSO, GA and DE under EC3 constraints
python -m pytest tests
```

`tests/test_analysis.py` checks that the linear run reproduces `examples/demo_12_joint/result.txt` and that nodal
displacements agree with the SAP2000 OAPI results (`sap2000_results.txt`) to 0.001 mm.

## Full agentic workflow

1. Set the LLM key (never commit it):
   ```bash
   set DEEPSEEK_API_KEY=sk-...        # Windows (PowerShell: $env:DEEPSEEK_API_KEY="sk-...")
   ```
2. Open a drawing in AutoCAD. Joints are blocks named `Joint`/`joint3D` with a `NODE` attribute; members are lines
   on layers named after the section (e.g. `CHS_114.3x3.6`); loads are `Load` blocks (magnitude + rotation,
   0 deg = downward); supports are `pinned_support`, `roller_support`, `X_support`, `Y_support`, `Z_support` blocks.
3. `python truss_chatbot.py`, then e.g. *"Extract the truss from AutoCAD"*, *"Run the analysis"*,
   *"Check the results against EC3"*, *"Optimize the truss"*, *"Draw the optimized truss"*.

### Eurocode 3 retrieval index

The Eurocode 3 documents are copyrighted and are **not** included. Convert your own copies with
[Marker](https://github.com/datalab-to/marker) into `rag_data/<name>/<name>.md` (see the header of
`rag_indexer.py`), then build the index:

```bash
python rag_indexer.py      # downloads Qwen/Qwen3-Embedding-0.6B on first use, writes the FAISS index
```

Without the index, every other agent still works; the optimizer applies the EC3 checks implemented in
`truss_optimizer.py` and does not need retrieval.

### SAP2000 cross-check

With SAP2000 open: `python sap2000_truss.py` builds the model from `truss.txt`, runs it and writes
`sap2000_results.txt`.

## Units and conventions

Input coordinates in m, loads in kN; results in mm, N and MPa (tension positive). Steel: E = 210 000 MPa,
f_y = 355 MPa, Steel01 with 2% hardening for material nonlinearity.

## License

MIT, see `LICENSE`.
