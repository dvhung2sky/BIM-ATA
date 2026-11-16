"""
LangChain Tools for Truss Analysis
Wraps existing AutoCAD and analysis functions for use with LLM agent
"""

import os

# Fix OpenMP duplicate library error on Windows (must be set before importing numpy/matplotlib)
os.environ['KMP_DUPLICATE_LIB_OK'] = 'TRUE'

import math
import pythoncom
from typing import Optional
from langchain_core.tools import tool

# Get the directory where this script is located
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
TRUSS_FILE = os.path.join(SCRIPT_DIR, "truss.txt")
RESULT_FILE = os.path.join(SCRIPT_DIR, "result.txt")
SAP2000_RESULT_FILE = os.path.join(SCRIPT_DIR, "sap2000_results.txt")
PLOT_FILE = os.path.join(SCRIPT_DIR, "analysis_plot.png")

# Global reference to chatbot app for displaying plots
_chatbot_app = None

def set_chatbot_app(app):
    """Set the chatbot app reference for displaying plots."""
    global _chatbot_app
    _chatbot_app = app

def display_plot_in_gui(plot_path, title="Plot"):
    """Display a plot in the GUI if available."""
    global _chatbot_app
    if _chatbot_app is not None:
        try:
            _chatbot_app.root.after(0, lambda: _chatbot_app.display_plot(plot_path, title))
            return True
        except Exception:
            pass
    return False


@tool
def get_truss_from_autocad() -> str:
    """
    Select and extract truss information from AutoCAD.

    This tool connects to AutoCAD, allows user to select truss elements,
    and extracts joint coordinates, elements, loads, and boundary conditions.
    The data is saved to truss.txt.

    Use this when the user wants to:
    - Select a truss from AutoCAD
    - Get truss information from AutoCAD
    - Load a truss from the drawing
    - Extract truss data

    Returns a summary of the extracted truss data.
    """
    try:
        # Initialize COM for this thread (required when running in background threads)
        pythoncom.CoInitialize()

        import win32com.client

        # Step 1: Connect to AutoCAD
        try:
            acad = win32com.client.Dispatch("AutoCAD.Application")
            doc = acad.ActiveDocument
            if doc is None:
                return "Error: AutoCAD is running but no document is open. Please open a drawing first."
        except Exception as e:
            return f"Error: Cannot connect to AutoCAD. Make sure AutoCAD is running.\nDetails: {str(e)}"

        # Step 2: Import and run the extraction
        import get_truss_joints

        # Call main which handles selection and extraction
        result = get_truss_joints.main()

        if result:
            joints, elements, loads, bcs = result
            return (
                f"Truss successfully extracted from AutoCAD!\n\n"
                f"Summary:\n"
                f"- Joints: {len(joints)}\n"
                f"- Elements: {len(elements)}\n"
                f"- Applied Loads: {len(loads)}\n"
                f"- Boundary Conditions: {len(bcs)}\n\n"
                f"Data saved to: truss.txt"
            )
        else:
            return (
                "Extraction returned no data. Possible causes:\n"
                "1. No elements were selected in AutoCAD\n"
                "2. Selected elements don't contain Joint blocks\n"
                "3. Selection was cancelled\n\n"
                "Please try again: Switch to AutoCAD, select ALL truss elements (including Joint blocks), then press Enter."
            )

    except Exception as e:
        import traceback
        return f"Error extracting truss from AutoCAD:\n{str(e)}\n\nTraceback:\n{traceback.format_exc()}"


OPTIMIZED_TRUSS_FILE = os.path.join(SCRIPT_DIR, "truss_optimized.txt")


@tool
def draw_truss_to_autocad(version: str = "original") -> str:
    """
    Draw the truss in AutoCAD at a user-selected location.

    This tool reads truss data and draws it in AutoCAD.
    The user will click in AutoCAD to select the insertion point.

    Use this when the user wants to:
    - Draw the truss in AutoCAD
    - Redraw the truss
    - Draw the optimized truss
    - Create truss drawing at a specific location

    Args:
        version: Which truss to draw:
            - "original": Draw the original truss from truss.txt (default)
            - "optimized": Draw the optimized truss from truss_optimized.txt
              (requires running optimization first)

    Returns a confirmation message.
    """
    try:
        # Initialize COM for this thread
        pythoncom.CoInitialize()

        if version.lower() == "optimized":
            source_file = OPTIMIZED_TRUSS_FILE
            label = "optimized"
            if not os.path.exists(source_file):
                return (
                    "Error: truss_optimized.txt not found. "
                    "Please run optimization first using 'optimize truss sections'."
                )
        else:
            source_file = TRUSS_FILE
            label = "original"
            if not os.path.exists(source_file):
                return "Error: truss.txt not found. Please extract truss from AutoCAD first using 'get truss from autocad'."

        import draw_truss_autocad

        # Parse the truss file
        truss_data = draw_truss_autocad.parse_truss_file(source_file)

        # Connect to AutoCAD
        acad, doc, model = draw_truss_autocad.connect_to_autocad()
        if doc is None:
            return "Error: Could not connect to AutoCAD. Make sure AutoCAD is running with a document open."

        # Get insertion point from user by mouse click
        base_point = draw_truss_autocad.get_point_from_user(doc)

        # Draw the truss at selected location
        draw_truss_autocad.draw_truss_in_autocad(doc, model, truss_data, base_point)

        return (
            f"{label.capitalize()} truss successfully drawn in AutoCAD!\n\n"
            f"Source: {os.path.basename(source_file)}\n"
            f"Base point: ({base_point[0]:.2f}, {base_point[1]:.2f}, {base_point[2]:.2f})\n"
            f"Elements drawn: {len(truss_data['elements'])}\n"
            f"Joints with NODE attribute: {len(truss_data['joints'])}"
        )

    except Exception as e:
        return f"Error drawing truss in AutoCAD: {str(e)}"


@tool
def run_truss_analysis(geometric_nonlinearity: bool = True, material_nonlinearity: bool = True) -> str:
    """
    Run 3D truss analysis using OpenSees.

    This tool analyzes the truss from truss.txt using finite element analysis
    with PyOpenSees. It calculates displacements and stresses in all members.
    Results are saved to result.txt.

    Use this when the user wants to:
    - Analyze the truss
    - Run structural analysis
    - Calculate stresses and displacements
    - Check if the truss is safe

    Args:
        geometric_nonlinearity: Include large displacement effects (default: True)
        material_nonlinearity: Include material yielding effects (default: True)

    Returns analysis summary with max displacement and stress.
    """
    try:
        if not os.path.exists(TRUSS_FILE):
            return "Error: truss.txt not found. Please extract truss from AutoCAD first."

        import truss3D_analysis_from_file

        displacements, stresses = truss3D_analysis_from_file.run_truss_analysis(
            TRUSS_FILE,
            RESULT_FILE,
            is_geom_nonlin=geometric_nonlinearity,
            is_mat_nonlin=material_nonlinearity
        )

        if displacements and stresses:
            # Find max displacement
            max_disp = 0
            max_disp_joint = 0
            for joint_id, (dx, dy, dz) in displacements.items():
                total = math.sqrt(dx**2 + dy**2 + dz**2)
                if total > max_disp:
                    max_disp = total
                    max_disp_joint = joint_id

            # Find max stress
            max_stress = 0
            max_stress_elem = 0
            for elem_id, stress in stresses.items():
                if abs(stress) > abs(max_stress):
                    max_stress = stress
                    max_stress_elem = elem_id

            stress_type = "Tension" if max_stress > 0 else "Compression"

            return (
                f"Analysis completed successfully!\n\n"
                f"Settings:\n"
                f"- Geometric Nonlinearity: {geometric_nonlinearity}\n"
                f"- Material Nonlinearity: {material_nonlinearity}\n\n"
                f"Results Summary:\n"
                f"- Maximum displacement: {max_disp:.6f} mm at Joint {max_disp_joint}\n"
                f"- Maximum stress: {abs(max_stress):.4f} MPa ({stress_type}) in Element {max_stress_elem}\n\n"
                f"Full results saved to: result.txt"
            )
        else:
            diag = truss3D_analysis_from_file.LAST_DIAGNOSTICS
            report = truss3D_analysis_from_file.format_diagnostics(diag) if diag else ""
            return ("Analysis failed. Explain the likely cause to the user and suggest how to repair "
                    "the model, using these pre-analysis diagnostics:\n" + (report or "(no diagnostics)"))

    except Exception as e:
        return f"Error running truss analysis: {str(e)}"


@tool
def query_truss_data(query: str) -> str:
    """
    Query truss data and analysis results.

    This tool reads truss.txt and result.txt to answer questions about:
    - Joint coordinates and displacements
    - Element properties, forces, and stresses
    - Applied loads
    - Boundary conditions
    - Section properties
    - Analysis summary

    Use this when the user asks questions like:
    - What is the stress in element X?
    - What is the displacement at joint Y?
    - How many joints/elements are there?
    - What loads are applied?
    - What are the boundary conditions?
    - List all elements with their stresses
    - What sections are used?

    Args:
        query: The user's question about the truss data

    Returns relevant information from the truss data files.
    """
    try:
        response_parts = []

        # Read truss.txt if it exists
        truss_data = None
        if os.path.exists(TRUSS_FILE):
            with open(TRUSS_FILE, 'r') as f:
                truss_content = f.read()
            response_parts.append("=== TRUSS INPUT DATA (truss.txt) ===\n")
            response_parts.append(truss_content)
        else:
            response_parts.append("Note: truss.txt not found. Extract truss from AutoCAD first.\n")

        # Read result.txt if it exists
        if os.path.exists(RESULT_FILE):
            with open(RESULT_FILE, 'r') as f:
                result_content = f.read()
            response_parts.append("\n\n=== ANALYSIS RESULTS (result.txt) ===\n")
            response_parts.append(result_content)
        else:
            response_parts.append("\nNote: result.txt not found. Run analysis first.\n")

        full_data = ''.join(response_parts)

        # Return the data for the LLM to process
        return f"Here is the truss data to answer the query '{query}':\n\n{full_data}"

    except Exception as e:
        return f"Error reading truss data: {str(e)}"


@tool
def check_autocad_connection() -> str:
    """
    Check if AutoCAD is running and accessible.

    Use this to verify AutoCAD connection before performing operations.

    Returns connection status.
    """
    try:
        # Initialize COM for this thread
        pythoncom.CoInitialize()

        import win32com.client
        acad = win32com.client.Dispatch("AutoCAD.Application")
        doc = acad.ActiveDocument

        if doc:
            return f"AutoCAD is connected!\nActive document: {doc.Name}"
        else:
            return "AutoCAD is running but no document is open. Please open a drawing."

    except Exception as e:
        return f"AutoCAD is not accessible: {str(e)}\nMake sure AutoCAD is running."


@tool
def plot_analysis_results(plot_type: str = "displacement") -> str:
    """
    Generate and display a plot of truss analysis results.

    This tool creates visualizations of the analysis results and displays them
    in the GUI's plot panel.

    Use this when the user wants to:
    - Plot displacement distribution
    - Visualize stress distribution
    - Show a bar chart of element forces
    - Display analysis results graphically
    - See a graph of the results

    Args:
        plot_type: Type of plot to generate. Options:
            - "displacement": Bar chart of joint displacements (default)
            - "stress": Bar chart of element stresses (tension/compression)
            - "force": Bar chart of element axial forces
            - "deformed": Schematic of deformed shape (if 2D)
            - "difference": Relative difference (%) of joint total displacement
              between OpenSees and SAP2000, using SAP2000 as reference.
              Fixed joints (SAP2000 total ≈ 0) are excluded.

    Returns a message indicating success or failure.
    """
    try:
        import matplotlib
        matplotlib.use('Agg')  # Non-interactive backend
        import matplotlib.pyplot as plt
        import numpy as np
    except ImportError:
        return "Cannot create plot: matplotlib is not installed. Run: pip install matplotlib"

    if not os.path.exists(RESULT_FILE):
        return "Cannot create plot: No analysis results found. Please run the analysis first."

    try:
        # Parse result.txt
        displacements = {}  # joint_id -> (dx, dy, dz, total)
        stresses = {}       # elem_id -> stress
        forces = {}         # elem_id -> force (if available)

        with open(RESULT_FILE, 'r') as f:
            content = f.read()

        # Parse joint displacements
        if "JOINT DISPLACEMENTS" in content:
            lines = content.split('\n')
            in_disp_section = False
            for line in lines:
                if "JOINT DISPLACEMENTS" in line:
                    in_disp_section = True
                    continue
                if in_disp_section:
                    if line.strip() == '' or line.startswith('=') or line.startswith('-'):
                        if line.startswith('=') and len(displacements) > 0:
                            break
                        continue
                    if line.strip().startswith('Joint'):
                        continue
                    parts = line.split()
                    if len(parts) >= 4:
                        try:
                            joint_id = int(parts[0])
                            dx = float(parts[1])
                            dy = float(parts[2])
                            dz = float(parts[3])
                            total = math.sqrt(dx**2 + dy**2 + dz**2)
                            displacements[joint_id] = (dx, dy, dz, total)
                        except (ValueError, IndexError):
                            continue

        # Parse element stresses
        if "ELEMENT STRESSES" in content or "ELEMENT RESULTS" in content:
            lines = content.split('\n')
            in_stress_section = False
            for line in lines:
                if "ELEMENT STRESSES" in line or "ELEMENT RESULTS" in line:
                    in_stress_section = True
                    continue
                if in_stress_section:
                    if line.strip() == '' or line.startswith('=') or line.startswith('-'):
                        if line.startswith('=') and len(stresses) > 0:
                            break
                        continue
                    if 'Element' in line and 'Stress' in line:
                        continue
                    parts = line.split()
                    if len(parts) >= 2:
                        try:
                            elem_id = int(parts[0])
                            stress = float(parts[1])
                            stresses[elem_id] = stress
                        except (ValueError, IndexError):
                            continue

        # Generate the requested plot
        fig, ax = plt.subplots(figsize=(10, 6))
        fig.patch.set_facecolor('#1e1e1e')
        ax.set_facecolor('#2d2d2d')

        # Style settings for dark theme
        ax.tick_params(colors='#d4d4d4')
        ax.xaxis.label.set_color('#d4d4d4')
        ax.yaxis.label.set_color('#d4d4d4')
        ax.title.set_color('#d4d4d4')
        for spine in ax.spines.values():
            spine.set_color('#555555')

        plot_title = ""

        if plot_type.lower() == "displacement":
            if not displacements:
                plt.close(fig)
                return "Cannot plot displacements: No displacement data found in results."

            joints = sorted(displacements.keys())
            totals = [displacements[j][3] for j in joints]

            colors = plt.cm.viridis(np.linspace(0.2, 0.8, len(joints)))
            bars = ax.bar([str(j) for j in joints], totals, color=colors, edgecolor='#4ec9b0')

            ax.set_xlabel('Joint ID', fontsize=11)
            ax.set_ylabel('Total Displacement (mm)', fontsize=11)
            plot_title = 'Joint Displacement Distribution'
            ax.set_title(plot_title, fontsize=14, fontweight='bold')

            # Add value labels on bars
            for bar, val in zip(bars, totals):
                ax.text(bar.get_x() + bar.get_width()/2, bar.get_height(),
                       f'{val:.4f}', ha='center', va='bottom', fontsize=8, color='#d4d4d4')

        elif plot_type.lower() == "stress":
            if not stresses:
                plt.close(fig)
                return "Cannot plot stresses: No stress data found in results."

            elements = sorted(stresses.keys())
            stress_vals = [stresses[e] for e in elements]

            # Color by tension (green) vs compression (red)
            colors = ['#4ec9b0' if s > 0 else '#f14c4c' for s in stress_vals]
            bars = ax.bar([str(e) for e in elements], stress_vals, color=colors, edgecolor='#555555')

            ax.set_xlabel('Element ID', fontsize=11)
            ax.set_ylabel('Stress (MPa)', fontsize=11)
            plot_title = 'Element Stress Distribution'
            ax.set_title(plot_title, fontsize=14, fontweight='bold')
            ax.axhline(y=0, color='#808080', linestyle='-', linewidth=0.5)

            # Add legend
            from matplotlib.patches import Patch
            legend_elements = [Patch(facecolor='#4ec9b0', label='Tension'),
                             Patch(facecolor='#f14c4c', label='Compression')]
            ax.legend(handles=legend_elements, loc='upper right', facecolor='#2d2d2d',
                     edgecolor='#555555', labelcolor='#d4d4d4')

        elif plot_type.lower() == "force":
            # For force, we'll use stress * area if available, or just show stress as proxy
            if not stresses:
                plt.close(fig)
                return "Cannot plot forces: No element data found in results."

            elements = sorted(stresses.keys())
            force_vals = [stresses[e] for e in elements]  # Using stress as proxy

            colors = ['#4ec9b0' if f > 0 else '#f14c4c' for f in force_vals]
            bars = ax.bar([str(e) for e in elements], force_vals, color=colors, edgecolor='#555555')

            ax.set_xlabel('Element ID', fontsize=11)
            ax.set_ylabel('Axial Force (proxy: stress MPa)', fontsize=11)
            plot_title = 'Element Axial Forces'
            ax.set_title(plot_title, fontsize=14, fontweight='bold')
            ax.axhline(y=0, color='#808080', linestyle='-', linewidth=0.5)

        elif plot_type.lower() == "difference":
            # ── Parse SAP2000 results ──────────────────────────────────────
            if not os.path.exists(SAP2000_RESULT_FILE):
                plt.close(fig)
                return "Cannot plot difference: sap2000_results.txt not found. Run SAP2000 analysis first."

            sap_disp = {}   # joint_id (int) -> (ux, uy, uz) mm
            with open(SAP2000_RESULT_FILE, 'r') as f:
                sap_content = f.read()

            in_sap_disp = False
            for line in sap_content.split('\n'):
                if "JOINT DISPLACEMENTS" in line:
                    in_sap_disp = True
                    continue
                if in_sap_disp:
                    if line.strip().startswith('=') and sap_disp:
                        break
                    if not line.strip() or line.strip().startswith('-') or 'Joint' in line:
                        continue
                    parts = line.split()
                    if len(parts) >= 4:
                        try:
                            # Joint name may be "J4" or "4"
                            jname_str = parts[0].lstrip('J')
                            jid = int(jname_str)
                            sap_disp[jid] = (float(parts[1]), float(parts[2]), float(parts[3]))
                        except (ValueError, IndexError):
                            continue

            if not sap_disp:
                plt.close(fig)
                return "Cannot plot difference: No displacement data found in sap2000_results.txt."

            if not displacements:
                plt.close(fig)
                return "Cannot plot difference: No displacement data found in result.txt. Run OpenSees analysis first."

            # ── Compute relative difference for joints with non-zero SAP2000 total ──
            ZERO_TOL = 1e-6   # mm — skip fixed/support joints
            diff_joints = []
            diff_pct    = []

            for jid in sorted(sap_disp.keys()):
                if jid not in displacements:
                    continue
                ux, uy, uz = sap_disp[jid]
                sap_total = math.sqrt(ux**2 + uy**2 + uz**2)
                if sap_total < ZERO_TOL:
                    continue   # fixed joint — skip
                dx, dy, dz, ops_total = displacements[jid]
                rel_diff = (ops_total - sap_total) / sap_total * 100.0
                diff_joints.append(str(jid))
                diff_pct.append(rel_diff)

            if not diff_joints:
                plt.close(fig)
                return "Cannot plot difference: No free joints with non-zero SAP2000 displacement found."

            # ── Bar chart ──────────────────────────────────────────────────
            colors = ['#f14c4c' if d < 0 else '#4ec9b0' for d in diff_pct]
            bars = ax.bar(diff_joints, diff_pct, color=colors, edgecolor='#555555')

            ax.axhline(y=0, color='#808080', linestyle='-', linewidth=0.8)
            ax.set_xlabel('Joint ID', fontsize=11)
            ax.set_ylabel('Relative Difference (%)', fontsize=11)
            plot_title = 'Joint Displacement: OpenSees vs SAP2000 (% diff, SAP2000 ref)'
            ax.set_title(plot_title, fontsize=12, fontweight='bold')

            # Value labels
            for bar, val in zip(bars, diff_pct):
                va = 'bottom' if val >= 0 else 'top'
                offset = 0.1 if val >= 0 else -0.1
                ax.text(bar.get_x() + bar.get_width() / 2,
                        bar.get_height() + offset,
                        f'{val:+.2f}%', ha='center', va=va, fontsize=7, color='#d4d4d4')

            from matplotlib.patches import Patch
            legend_elements = [Patch(facecolor='#4ec9b0', label='OpenSees > SAP2000'),
                               Patch(facecolor='#f14c4c', label='OpenSees < SAP2000')]
            ax.legend(handles=legend_elements, loc='upper right', facecolor='#2d2d2d',
                      edgecolor='#555555', labelcolor='#d4d4d4')

        else:
            plt.close(fig)
            return f"Unknown plot type: '{plot_type}'. Valid options: displacement, stress, force, difference"

        plt.tight_layout()

        # Save the plot
        fig.savefig(PLOT_FILE, dpi=150, facecolor='#1e1e1e', edgecolor='none',
                   bbox_inches='tight', pad_inches=0.1)
        plt.close(fig)

        # Try to display in GUI
        displayed = display_plot_in_gui(PLOT_FILE, plot_title)

        if displayed:
            return f"Plot generated and displayed: {plot_title}"
        else:
            return f"Plot saved to: {PLOT_FILE}\n(Note: GUI display not available, but file was saved)"

    except Exception as e:
        return f"Error generating plot: {str(e)}"


@tool
def assess_eurocode_compliance(check_type: str = "all") -> str:
    """
    Assess truss analysis results against Eurocode 3 (EN 1993-1-1).

    Retrieves relevant clauses, equations, and worked examples from:
    - EN 1993-1-1 (Eurocode 3: Design of steel structures)
    - Truss design worked examples and tutorials

    Uses RAG (Retrieval-Augmented Generation) with FAISS vector search
    to find the most relevant design checks for the specific sections
    and stress levels in the analysis results.

    Use this when the user wants to:
    - Check if the truss passes Eurocode 3
    - Verify structural safety against design codes
    - Perform buckling, tension, or compression checks
    - Assess compliance with deflection limits
    - Get unity checks for each member

    Args:
        check_type: Type of assessment to perform. Options:
            - "all": Full compliance check (tension, compression, buckling, serviceability)
            - "tension": Check tension members against EC3 §6.2.3
            - "compression": Check compression members against EC3 §6.2.4
            - "buckling": Check buckling resistance against EC3 §6.3
            - "serviceability": Check deflection limits against EC3 §7.2
            - "slenderness": Check member slenderness ratios

    Returns relevant Eurocode clauses, equations, and assessment context
    for the LLM to evaluate compliance.
    """
    try:
        if not os.path.exists(RESULT_FILE):
            return (
                "Error: result.txt not found. Please run the structural analysis first "
                "using 'run truss analysis' before assessing Eurocode compliance."
            )

        import rag_retriever

        results_summary, context = rag_retriever.assess_results(
            check_type=check_type,
            top_k_per_query=3,
        )

        if not context:
            return (
                f"Results Summary:\n{results_summary}\n\n"
                "Warning: No Eurocode context retrieved. "
                "Make sure the RAG index is built (run rag_indexer.py first)."
            )

        return (
            f"=== ANALYSIS RESULTS SUMMARY ===\n{results_summary}\n\n"
            f"=== RELEVANT EUROCODE 3 CLAUSES & EXAMPLES ===\n{context}\n\n"
            f"Based on the above Eurocode 3 references, assess whether each member "
            f"satisfies the design checks. For each check:\n"
            f"1. State the governing EC3 clause and equation number\n"
            f"2. Show the numerical calculation and unity check (N_Ed / N_Rd ≤ 1.0)\n"
            f"3. Mark PASS or FAIL for each member\n"
            f"4. Summarise overall compliance status"
        )

    except FileNotFoundError as e:
        return (
            f"RAG index not built: {str(e)}\n"
            "Run 'python rag_indexer.py' to build the index first."
        )
    except Exception as e:
        return f"Error assessing Eurocode compliance: {str(e)}"


@tool
def optimize_truss_sections(
    mode: str = "discrete",
    section_csv: str = "CHS_section.csv",
    algorithm: str = "PSO",
    pop_size: int = 50,
    n_gen: int = 100,
) -> str:
    """Optimize truss section sizes to minimize total mass while satisfying
    Eurocode 3 safety criteria (tension §6.2.3, compression §6.2.4,
    buckling §6.3.1, slenderness λ≤200, deflection L/250).

    Uses pymoo optimization library with PSO, GA, or DE.

    Args:
        mode: "discrete" to pick from CHS catalog, or "continuous" to optimize
              area directly (derives CHS properties from area assuming d/t=25)
        section_csv: Path to section catalog CSV file (default: CHS_section.csv)
        algorithm: "PSO" (Particle Swarm, default), "GA" (Genetic Algorithm),
                   "DE" (Differential Evolution), or "ALL" (runs PSO+GA+DE and
                   picks the best result)
        pop_size: Population size for the optimization algorithm
        n_gen: Number of generations to run

    Returns:
        Formatted report with optimal sections per group, mass reduction,
        and EC3 constraint verification. Does NOT modify truss.txt.
    """
    try:
        from truss_optimizer import optimize_truss

        result = optimize_truss(
            truss_file="truss.txt",
            section_csv=section_csv,
            mode=mode,
            algorithm=algorithm,
            pop_size=pop_size,
            n_gen=n_gen,
            seed=42,
        )
        return result['report']

    except ImportError as e:
        return f"Optimizer not available: {str(e)}. Install pymoo: pip install -U pymoo"
    except Exception as e:
        return f"Optimization error: {str(e)}"


@tool
def convert_bars_to_3d_solids() -> str:
    """
    Convert truss bar line elements to 3D solid cylinders in AutoCAD.

    Prompts the user to select truss elements in AutoCAD. For each selected
    line element, creates a circle of radius 0.03 at the start point
    (perpendicular to the bar direction), then sweeps it along the bar to
    produce a 3D solid. The solid is placed on the same layer as the
    original truss bar.

    Use this when the user wants to:
    - Convert truss lines to 3D solid cylinders
    - Create a 3D solid / solid model of the truss
    - Generate a 3D tube or pipe representation of truss bars
    - Visualize truss cross-sections as 3D solids in AutoCAD

    Returns a summary of how many elements were converted.
    """
    try:
        pythoncom.CoInitialize()
        import win32com.client
        from win32com.client import VARIANT

        def _apt(x, y, z):
            """Create a 3D point VARIANT for AutoCAD COM."""
            return VARIANT(pythoncom.VT_ARRAY | pythoncom.VT_R8, (x, y, z))

        # ── Connect to AutoCAD ──────────────────────────────────────────────
        try:
            acad = win32com.client.Dispatch("AutoCAD.Application")
            doc = acad.ActiveDocument
            if doc is None:
                return "Error: AutoCAD is running but no document is open."
        except Exception as e:
            return f"Error: Cannot connect to AutoCAD. Make sure AutoCAD is running.\nDetails: {str(e)}"

        model = doc.ModelSpace

        # ── Selection set ───────────────────────────────────────────────────
        ss_name = "TrussBars3DSolids"
        try:
            doc.SelectionSets.Item(ss_name).Delete()
        except Exception:
            pass
        ss = doc.SelectionSets.Add(ss_name)

        # Bring AutoCAD to front so the user can select
        try:
            acad.Visible = True
            acad.WindowState = 3  # acMax
        except Exception:
            pass

        ss.SelectOnScreen()

        # Give AutoCAD time to leave selection mode before making further COM calls.
        import time
        time.sleep(0.5)
        try:
            pythoncom.PumpWaitingMessages()
        except Exception:
            pass

        n_total = ss.Count
        if n_total == 0:
            try:
                ss.Delete()
            except Exception:
                pass
            return "No elements selected. Please select truss bar elements and try again."

        # ── Save AutoCAD state ───────────────────────────────────────────────
        try:
            original_layer = doc.GetVariable("CLAYER")
        except Exception:
            original_layer = "0"
        try:
            original_delobj = doc.GetVariable("DELOBJ")
            doc.SetVariable("DELOBJ", 1)   # delete profile entity after sweep
        except Exception:
            original_delobj = None

        # ── Process each line ───────────────────────────────────────────────
        # Strategy: create circle via COM, then SWEEP via AutoLISP SendCommand.
        # SendCommand + (command "._SWEEP" ...) is synchronous within LISP, so
        # no sleep needed between elements.  SWEEP auto-aligns the profile
        # perpendicular to the path (Alignment option = default), and the solid
        # inherits the current layer (CLAYER set before each SWEEP).
        n_converted = 0
        n_skipped = 0
        errors = []

        # Use ss.Item(i) instead of "for entity in ss:" — the COM IEnumVARIANT
        # iterator raises RPC_E_CALL_REJECTED when AutoCAD is still transitioning.
        # Direct indexed access is a plain method call that can be retried.
        for i in range(n_total):
            circle = None  # track for cleanup on failure

            # Retry loop: up to 20 attempts on any COM rejection.
            for _attempt in range(20):
                try:
                    entity = ss.Item(i)

                    # Skip non-line and proxy objects ──────────────────────────
                    try:
                        ename = entity.EntityName
                    except AttributeError:
                        n_skipped += 1
                        errors.append(f"Bar {i}: no EntityName (proxy object), skipped")
                        break   # not a retriable error

                    # Accept AcDbLine and AcDb3dPolyline (visually identical)
                    if ename not in ("AcDbLine", "AcDb3dPolyline"):
                        n_skipped += 1
                        errors.append(f"Bar {i}: unsupported type '{ename}', skipped")
                        break

                    # Read bar geometry ────────────────────────────────────────
                    layer_name = entity.Layer
                    if ename == "AcDbLine":
                        sp = entity.StartPoint
                        ep = entity.EndPoint
                    else:
                        # AcDb3dPolyline: Coordinates is a flat [x1,y1,z1, x2,y2,z2, ...]
                        coords = entity.Coordinates
                        sp = (coords[0], coords[1], coords[2])
                        ep = (coords[-3], coords[-2], coords[-1])

                    dx = ep[0] - sp[0]
                    dy = ep[1] - sp[1]
                    dz = ep[2] - sp[2]
                    length = math.sqrt(dx**2 + dy**2 + dz**2)

                    if length < 1e-10:
                        n_skipped += 1
                        break

                    # Unit vector along bar = circle normal (plane ⊥ to bar)
                    ux = dx / length
                    uy = dy / length
                    uz = dz / length

                    # Set current layer → solid inherits it after SWEEP
                    doc.SetVariable("CLAYER", layer_name)

                    # 1. Circle at StartPoint, normal = bar direction ──────────
                    circle = model.AddCircle(_apt(sp[0], sp[1], sp[2]), 0.03)
                    circle.Normal = VARIANT(
                        pythoncom.VT_ARRAY | pythoncom.VT_R8, [ux, uy, uz]
                    )

                    circle_handle = circle.Handle
                    line_handle   = entity.Handle

                    # 2. SWEEP circle along bar via AutoLISP ──────────────────
                    #    "._SWEEP" = English command regardless of locale
                    #    ""        = end profile selection (Enter)
                    #    SWEEP completes automatically after the path is picked
                    doc.SendCommand(
                        f'(command "._SWEEP"'
                        f' (handent "{circle_handle}") ""'
                        f' (handent "{line_handle}")) \n'
                    )

                    n_converted += 1
                    break   # ── success, move to next bar ────────────────────

                except Exception as e:
                    # Clean up any partially created circle before retrying
                    if circle is not None:
                        try:
                            circle.Delete()
                        except Exception:
                            pass
                        circle = None

                    if _attempt < 19:
                        time.sleep(0.3)   # brief pause then retry
                    else:
                        errors.append(
                            f"Bar {i} on layer '{getattr(entity, 'Layer', '?')}'"
                            f" (gave up after 20 attempts): {str(e)}"
                        )
                        n_skipped += 1

        # ── Restore AutoCAD state ────────────────────────────────────────────
        try:
            doc.SetVariable("CLAYER", original_layer)
        except Exception:
            pass
        if original_delobj is not None:
            try:
                doc.SetVariable("DELOBJ", original_delobj)
            except Exception:
                pass

        try:
            ss.Delete()
        except Exception:
            pass

        try:
            doc.Regen(1)   # acAllViewports
        except Exception:
            pass

        result = (
            f"3D Solid Conversion Complete!\n\n"
            f"Selected elements : {n_total}\n"
            f"Converted to 3D solids : {n_converted}\n"
            f"Skipped (non-line / zero-length) : {n_skipped}\n"
        )
        if errors:
            result += f"\nSkip / error details ({len(errors)}):\n"
            for err in errors[:10]:
                result += f"  - {err}\n"
            if len(errors) > 10:
                result += f"  ... and {len(errors) - 10} more\n"

        return result

    except Exception as e:
        import traceback
        return (
            f"Error converting bars to 3D solids:\n{str(e)}\n\n"
            f"Traceback:\n{traceback.format_exc()}"
        )


# List of all tools for the agent
ALL_TOOLS = [
    get_truss_from_autocad,
    draw_truss_to_autocad,
    run_truss_analysis,
    query_truss_data,
    check_autocad_connection,
    plot_analysis_results,
    assess_eurocode_compliance,
    optimize_truss_sections,
    convert_bars_to_3d_solids,
]
