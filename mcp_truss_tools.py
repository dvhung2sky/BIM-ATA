"""
MCP (Model Context Protocol) Tool Definitions for Structural Truss Analysis Agent

This module exposes truss analysis capabilities as MCP tools so that any
LLM orchestration layer can discover and invoke them via the MCP protocol.

Workflow:
  1. check_autocad_connection    → verify AutoCAD is reachable
  2. get_truss_from_autocad      → select & extract truss geometry from AutoCAD
  3. run_truss_analysis          → run FEA with OpenSees
  4. query_truss_data            → ask questions about the model or results
  5. draw_truss_to_autocad       → redraw (possibly modified) truss back in AutoCAD
  6. plot_analysis_results       → visualize displacements, stresses, or forces
  7. assess_eurocode_compliance  → RAG-based Eurocode 3 compliance assessment
"""

TRUSS_MCP_TOOLS = [
    {
        "name": "check_autocad_connection",
        "description": (
            "Check whether AutoCAD is running and a drawing document is open. "
            "Call this before any tool that interacts with AutoCAD "
            "(get_truss_from_autocad, draw_truss_to_autocad) to ensure "
            "the connection is available. Returns the active document name "
            "on success, or an error message if AutoCAD is not accessible."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {},
            "required": []
        }
    },
    {
        "name": "get_truss_from_autocad",
        "description": (
            "Extract structural truss data from the active AutoCAD drawing. "
            "The user is prompted to select truss elements (lines, joint blocks, "
            "load blocks, and boundary condition blocks) with the mouse in AutoCAD. "
            "The tool then parses the selection and exports: "
            "(1) joint coordinates, "
            "(2) element connectivity with section/layer assignment, "
            "(3) applied loads with magnitude, direction, and point of application, "
            "(4) boundary conditions with constrained DOFs. "
            "Output is saved to 'truss.txt' as the canonical intermediate file "
            "consumed by analysis and redraw tools. "
            "\n\n"
            "Trigger phrases: "
            "'select truss', 'extract truss', 'get truss from AutoCAD', "
            "'read the drawing', 'load truss data', 'pick the truss'."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {},
            "required": []
        }
    },
    {
        "name": "run_truss_analysis",
        "description": (
            "Run 3D finite element analysis on the truss stored in 'truss.txt' "
            "using OpenSees (via openseespy). Computes nodal displacements and "
            "element axial stresses under the applied loads and boundary conditions. "
            "Supports optional geometric nonlinearity (P-delta / large displacement) "
            "and material nonlinearity (steel yielding). "
            "Results are written to 'result.txt' containing per-joint displacements "
            "(dX, dY, dZ) and per-element stresses (MPa). "
            "Requires 'truss.txt' to exist — call get_truss_from_autocad first. "
            "\n\n"
            "Trigger phrases: "
            "'analyze the truss', 'run analysis', 'calculate stresses', "
            "'compute displacements', 'check structural safety', 'run FEA'."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "geometric_nonlinearity": {
                    "type": "boolean",
                    "default": True,
                    "description": (
                        "Enable geometric nonlinearity (P-delta and large "
                        "displacement effects). Set to False for a simple "
                        "linear elastic analysis."
                    )
                },
                "material_nonlinearity": {
                    "type": "boolean",
                    "default": True,
                    "description": (
                        "Enable material nonlinearity (steel yielding with "
                        "hardening). Set to False to assume perfectly elastic "
                        "material behaviour."
                    )
                }
            },
            "required": []
        }
    },
    {
        "name": "query_truss_data",
        "description": (
            "Retrieve and return the full contents of 'truss.txt' (model input) "
            "and 'result.txt' (analysis output) so the LLM can answer the user's "
            "question about the structural model. Use this tool whenever the user "
            "asks a factual question about the truss — for example: joint coordinates, "
            "element connectivity, section areas, applied load values, boundary "
            "condition types, displacement at a specific joint, stress in a specific "
            "element, maximum/minimum values, or any comparison between members. "
            "The tool returns raw text data; the LLM should parse it and formulate "
            "the answer. "
            "\n\n"
            "Trigger phrases: "
            "'what is the stress in element 5', 'displacement at joint 3', "
            "'how many elements', 'list all loads', 'which element has the highest stress', "
            "'what sections are used', 'show boundary conditions'."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": (
                        "The user's natural-language question about the truss "
                        "geometry, loads, boundary conditions, or analysis results."
                    )
                }
            },
            "required": ["query"]
        }
    },
    {
        "name": "draw_truss_to_autocad",
        "description": (
            "Redraw the truss in AutoCAD from the data in 'truss.txt'. "
            "The user clicks a point in AutoCAD to set the insertion origin; "
            "the tool then draws all elements (as 3D lines on section-named layers "
            "with original colours), joint blocks (with node ID attributes), "
            "load blocks (with correct 3D orientation, scale, and value text), "
            "and boundary condition blocks. "
            "Use this after modifying truss data or to duplicate the truss at "
            "a new location. Requires 'truss.txt' and an open AutoCAD document. "
            "\n\n"
            "Trigger phrases: "
            "'draw the truss', 'redraw truss', 'recreate the drawing', "
            "'place truss in AutoCAD', 'draw truss at a new location'."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {},
            "required": []
        }
    },
    {
        "name": "plot_analysis_results",
        "description": (
            "Generate a bar-chart visualisation of the analysis results and "
            "display it in the GUI plot panel (or save to 'analysis_plot.png'). "
            "Requires 'result.txt' to exist — call run_truss_analysis first. "
            "Available plot types: "
            "'displacement' — total displacement per joint, "
            "'stress' — axial stress per element (green = tension, red = compression), "
            "'force' — axial force per element, "
            "'difference' — relative difference (%) of joint total displacement "
            "between OpenSees and SAP2000, using SAP2000 as reference "
            "(requires both result.txt and sap2000_results.txt). "
            "\n\n"
            "Trigger phrases: "
            "'plot displacements', 'show stress chart', 'visualize results', "
            "'graph the forces', 'display analysis plot', "
            "'compare opensees and sap2000', 'show difference plot'."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "plot_type": {
                    "type": "string",
                    "enum": ["displacement", "stress", "force", "difference"],
                    "default": "displacement",
                    "description": (
                        "Type of result to plot. "
                        "'displacement' — bar chart of total joint displacements (mm). "
                        "'stress' — bar chart of element axial stresses (MPa). "
                        "'force' — bar chart of element axial forces. "
                        "'difference' — relative difference (%) between OpenSees and "
                        "SAP2000 total joint displacements (SAP2000 as reference); "
                        "fixed joints are excluded automatically."
                    )
                }
            },
            "required": []
        }
    },
    {
        "name": "assess_eurocode_compliance",
        "description": (
            "Assess truss analysis results against Eurocode 3 (EN 1993-1-1) "
            "using Retrieval-Augmented Generation (RAG). The tool reads "
            "'result.txt', builds targeted queries from the actual member "
            "stresses, sections, and displacements, then retrieves the most "
            "relevant clauses, equations (in LaTeX), and worked examples from "
            "a FAISS vector index of Eurocode 3 and truss design reference PDFs. "
            "The retrieved context is returned alongside the results summary for "
            "the LLM to perform unity checks and compliance assessment. "
            "Supported checks: tension resistance (§6.2.3), compression "
            "resistance (§6.2.4), flexural buckling (§6.3.1), serviceability "
            "deflection limits (§7.2), and member slenderness ratios. "
            "Requires 'result.txt' — call run_truss_analysis first, and the "
            "FAISS index must be built by running rag_indexer.py. "
            "\n\n"
            "Trigger phrases: "
            "'check eurocode', 'is it safe', 'compliance check', "
            "'assess the results', 'check against EC3', 'buckling check', "
            "'does it pass eurocode', 'unity check', 'design verification'."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "check_type": {
                    "type": "string",
                    "enum": ["all", "tension", "compression", "buckling",
                             "serviceability", "slenderness"],
                    "default": "all",
                    "description": (
                        "Type of Eurocode 3 design check to perform. "
                        "'all' — full compliance check (tension, compression, "
                        "buckling, serviceability). "
                        "'tension' — check tension members against §6.2.3. "
                        "'compression' — check compression members against §6.2.4. "
                        "'buckling' — check buckling resistance against §6.3.1. "
                        "'serviceability' — check deflection limits against §7.2. "
                        "'slenderness' — check member slenderness ratios."
                    )
                }
            },
            "required": []
        }
    }
]


# ---------------------------------------------------------------------------
#  MCP Server implementation (stdio transport)
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import json
    import sys

    # Print tool listing for quick inspection
    print("=" * 60)
    print("TRUSS ANALYSIS MCP TOOLS")
    print("=" * 60)
    for t in TRUSS_MCP_TOOLS:
        params = list(t["inputSchema"].get("properties", {}).keys())
        param_str = f"({', '.join(params)})" if params else "()"
        print(f"\n  {t['name']}{param_str}")
        # Print first sentence of description
        desc = t["description"].split(". ")[0] + "."
        print(f"    {desc}")

    print(f"\n{len(TRUSS_MCP_TOOLS)} tools available.")
    print("\nFull JSON schema:")
    print(json.dumps(TRUSS_MCP_TOOLS, indent=2))
