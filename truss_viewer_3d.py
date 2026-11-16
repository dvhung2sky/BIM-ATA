"""
Interactive 3D Plotly Truss Viewer
Builds a dual-mode (Categorical / Analytical) 3D visualization of the truss
with hover tooltips showing Element ID, Section, Utilization, and Cost.
"""

import os
import math
import plotly.graph_objects as go

from truss3D_analysis_from_file import read_truss_file
from truss_optimizer import (
    load_section_catalog,
    get_section_groups,
    chs_properties_from_area,
    check_tension_unity,
    check_buckling_unity,
    FY, E, GAMMA_M0, GAMMA_M1, RHO_STEEL,
)

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
TRUSS_FILE = os.path.join(SCRIPT_DIR, "truss.txt")
RESULT_FILE = os.path.join(SCRIPT_DIR, "result.txt")
CHS_CSV = os.path.join(SCRIPT_DIR, "CHS_eurocode.csv")

STEEL_PRICE_PER_KG = 1.00  # USD/kg default


# ---------------------------------------------------------------------------
#  Data loading
# ---------------------------------------------------------------------------

def load_truss_data(truss_path=None):
    """Load parsed truss data from truss.txt."""
    path = truss_path or TRUSS_FILE
    return read_truss_file(path)


def load_analysis_results(result_path=None):
    """
    Parse result.txt: MEMBER AXIAL FORCES AND STRESSES + EC3 UTILIZATION FACTORS.
    Returns {elem_id: {'force_N': float, 'stress_MPa': float, 'area_mm2': float,
                        'section_name': str, 'utilization': float}}
    """
    path = result_path or RESULT_FILE
    results = {}
    if not os.path.exists(path):
        return results

    with open(path, 'r') as f:
        content = f.read()

    # Parse forces/stresses and utilization
    current_section = None
    for line in content.split('\n'):
        stripped = line.strip()

        # Section headers (take priority over === detection)
        if 'MEMBER AXIAL FORCES AND STRESSES' in stripped:
            current_section = 'forces'
            continue
        elif 'EC3 UTILIZATION FACTORS' in stripped:
            current_section = 'utilization'
            continue
        elif 'ANALYSIS SUMMARY' in stripped:
            break  # done parsing

        # Skip separators and headers
        if stripped.startswith('===') or stripped.startswith('-') or not stripped:
            continue
        if stripped.startswith('Elem') or stripped.startswith('Note:'):
            continue

        parts = stripped.split()

        if current_section == 'forces' and len(parts) >= 5:
            try:
                elem_id = int(parts[0])
                results[elem_id] = {
                    'force_N': float(parts[3]),
                    'stress_MPa': float(parts[4]),
                    'area_mm2': float(parts[2]),
                    'section_name': parts[1],
                }
            except (ValueError, IndexError):
                continue

        elif current_section == 'utilization' and len(parts) >= 5:
            try:
                elem_id = int(parts[0])
                utilization = float(parts[4])
                if elem_id in results:
                    results[elem_id]['utilization'] = utilization
            except (ValueError, IndexError):
                continue

    return results


def _find_closest_catalog_entry(area_mm2, catalog):
    """Find CHS catalog entry closest to given area."""
    best = None
    best_diff = float('inf')
    for sec in catalog:
        diff = abs(sec['A'] - area_mm2)
        if diff < best_diff:
            best_diff = diff
            best = sec
    return best


# ---------------------------------------------------------------------------
#  Element data computation (utilization + cost)
# ---------------------------------------------------------------------------

def compute_element_data(truss_data, analysis_results, catalog=None):
    """
    Merge geometry, analysis results, and EC3 utilization for each element.
    Uses pre-computed utilization from result file if available.
    Returns list of dicts with keys: elem_id, section_name, section_group,
    start, end, length_mm, force_N, area_mm2, utilization, cost_eur, mass_kg.
    """
    if catalog is None:
        if os.path.exists(CHS_CSV):
            catalog = load_section_catalog(CHS_CSV)
        else:
            catalog = []

    elements_data = []
    for elem_id, start, end, section_name in truss_data['elements']:
        dx = end[0] - start[0]
        dy = end[1] - start[1]
        dz = end[2] - start[2]
        L_m = math.sqrt(dx**2 + dy**2 + dz**2)  # meters (truss.txt coords are in meters)
        L_mm = L_m * 1000.0

        # Get force and area from analysis results
        res = analysis_results.get(elem_id, {})
        N_Ed = res.get('force_N', 0.0)
        A_mm2 = res.get('area_mm2', 0.0)

        # Fallback: parse area from section name (e.g. "A6_1.298" -> 1.298 cm2 -> 129.8 mm2)
        if A_mm2 <= 0:
            parts = section_name.rsplit('_', 1)
            if len(parts) == 2:
                try:
                    A_mm2 = float(parts[1]) * 100.0  # cm2 -> mm2
                except ValueError:
                    pass

        # Use pre-computed utilization from result file if available
        utilization = res.get('utilization', None)
        if utilization is None:
            # Fallback: recompute (for old result files without utilization section)
            i_mm = 0.0
            if catalog and A_mm2 > 0:
                closest = _find_closest_catalog_entry(A_mm2, catalog)
                if closest:
                    i_mm = closest['i']
            if i_mm <= 0 and A_mm2 > 0:
                props = chs_properties_from_area(A_mm2)
                i_mm = props['i']

            t_unity = check_tension_unity(N_Ed, A_mm2) if N_Ed > 0 else 0.0
            b_unity = check_buckling_unity(N_Ed, A_mm2, L_mm, i_mm) if N_Ed < 0 else 0.0
            utilization = max(t_unity, b_unity)

        # Cost
        mass_kg = RHO_STEEL * (A_mm2 * 1e-6) * L_m  # kg
        cost_eur = mass_kg * STEEL_PRICE_PER_KG

        # Section group prefix
        parts = section_name.rsplit('_', 1)
        group = parts[0] if len(parts) == 2 else section_name

        elements_data.append({
            'elem_id': elem_id,
            'section_name': section_name,
            'section_group': group,
            'start': start,
            'end': end,
            'length_mm': L_mm,
            'force_N': N_Ed,
            'area_mm2': A_mm2,
            'utilization': utilization,
            'cost_eur': cost_eur,
            'mass_kg': mass_kg,
        })

    return elements_data


# ---------------------------------------------------------------------------
#  Utilization -> color mapping
# ---------------------------------------------------------------------------

def _utilization_color(u):
    """Map utilization 0..2+ to green->orange->red RGB string.
    0=green, ~1=orange, >1=red, >=2=dark red."""
    u = max(0.0, min(u, 2.0))
    if u <= 0.5:
        # green -> yellow-green
        r = int(200 * (u / 0.5))
        g = 200
        b = 0
    elif u <= 1.0:
        # yellow-green -> orange
        t = (u - 0.5) / 0.5
        r = 200 + int(55 * t)
        g = int(200 * (1.0 - t * 0.5))  # 200 -> 100 (orange)
        b = 0
    elif u <= 2.0:
        # orange -> dark red
        t = (u - 1.0) / 1.0
        r = 255 - int(55 * t)  # 255 -> 200
        g = int(100 * (1.0 - t))  # 100 -> 0
        b = 0
    else:
        r = 200
        g = 0
        b = 0
    return f'rgb({r},{g},{b})'


# ---------------------------------------------------------------------------
#  Plotly figure builder
# ---------------------------------------------------------------------------

# Discrete color palette for section groups
_CATEGORICAL_COLORS = [
    '#1f77b4', '#ff7f0e', '#2ca02c', '#d62728', '#9467bd',
    '#8c564b', '#e377c2', '#7f7f7f', '#bcbd22', '#17becf',
    '#aec7e8', '#ffbb78', '#98df8a', '#ff9896', '#c5b0d5',
    '#c49c94', '#f7b6d2', '#c7c7c7', '#dbdb8d', '#9edae5',
    '#393b79', '#637939', '#8c6d31', '#843c39', '#7b4173',
]


def build_plotly_figure(elements_data):
    """
    Build Plotly 3D figure with dual view modes:
    - Categorical: bars colored by section group (discrete colors)
    - Analytical: bars colored by utilization factor (green->yellow->red gradient)

    View mode toggled via Plotly updatemenus dropdown (pure JS, no Python round-trip).
    """
    fig = go.Figure()

    # --- Categorical traces (one per section group) ---
    groups = {}
    for ed in elements_data:
        g = ed['section_group']
        if g not in groups:
            groups[g] = []
        groups[g].append(ed)

    sorted_groups = sorted(groups.keys(), key=lambda n: (
        [int(p) if p.isdigit() else p.lower() for p in __import__('re').split(r'(\d+)', n)]
    ))

    n_categorical = 0
    for idx, group_name in enumerate(sorted_groups):
        elems = groups[group_name]
        xs, ys, zs = [], [], []
        hover_texts = []
        for ed in elems:
            xs += [ed['start'][0], ed['end'][0], None]
            ys += [ed['start'][1], ed['end'][1], None]
            zs += [ed['start'][2], ed['end'][2], None]
            ht = (
                f"<b>Element {ed['elem_id']}</b><br>"
                f"Section: {ed['section_name']}<br>"
                f"Utilization: {ed['utilization']:.3f}<br>"
                f"Cost: ${ed['cost_eur']:.2f}"
            )
            hover_texts += [ht, ht, None]

        color = _CATEGORICAL_COLORS[idx % len(_CATEGORICAL_COLORS)]
        fig.add_trace(go.Scatter3d(
            x=xs, y=ys, z=zs,
            mode='lines',
            name=group_name,
            line=dict(width=6, color=color),
            hovertext=hover_texts,
            hoverinfo='text',
            visible=True,
        ))
        n_categorical += 1

    # --- Analytical traces (one per element, colored by utilization) ---
    n_analytical = 0
    for ed in elements_data:
        color = _utilization_color(ed['utilization'])
        ht = (
            f"<b>Element {ed['elem_id']}</b><br>"
            f"Section: {ed['section_name']}<br>"
            f"Utilization: {ed['utilization']:.3f}<br>"
            f"Cost: ${ed['cost_eur']:.2f}"
        )
        fig.add_trace(go.Scatter3d(
            x=[ed['start'][0], ed['end'][0]],
            y=[ed['start'][1], ed['end'][1]],
            z=[ed['start'][2], ed['end'][2]],
            mode='lines',
            line=dict(width=6, color=color),
            hovertext=[ht, ht],
            hoverinfo='text',
            name=f"U={ed['utilization']:.2f}",
            showlegend=False,
            visible=False,
        ))
        n_analytical += 1

    # --- Joint markers (always visible) ---
    joint_set = {}
    for ed in elements_data:
        for pt in [ed['start'], ed['end']]:
            key = (round(pt[0], 4), round(pt[1], 4), round(pt[2], 4))
            if key not in joint_set:
                joint_set[key] = pt

    jx = [p[0] for p in joint_set.values()]
    jy = [p[1] for p in joint_set.values()]
    jz = [p[2] for p in joint_set.values()]

    fig.add_trace(go.Scatter3d(
        x=jx, y=jy, z=jz,
        mode='markers',
        marker=dict(size=4, color='#333333'),
        name='Joints',
        hoverinfo='text',
        hovertext=[f"({x:.3f}, {y:.3f}, {z:.3f})" for x, y, z in zip(jx, jy, jz)],
        visible=True,
    ))
    n_joints = 1

    # --- Utilization color bar (dummy invisible scatter for the colorbar) ---
    fig.add_trace(go.Scatter3d(
        x=[None], y=[None], z=[None],
        mode='markers',
        marker=dict(
            size=0.1,
            color=[0],
            colorscale=[[0, 'rgb(0,200,0)'], [0.25, 'rgb(200,200,0)'], [0.5, 'rgb(255,100,0)'], [0.75, 'rgb(255,0,0)'], [1, 'rgb(200,0,0)']],
            cmin=0, cmax=2,
            colorbar=dict(title='Utilization', thickness=15, len=0.5, x=1.02),
            showscale=True,
        ),
        hoverinfo='none',
        showlegend=False,
        visible=False,
    ))
    n_colorbar = 1

    # --- View mode dropdown ---
    cat_vis = [True]*n_categorical + [False]*n_analytical + [True]*n_joints + [False]*n_colorbar
    ana_vis = [False]*n_categorical + [True]*n_analytical + [True]*n_joints + [True]*n_colorbar

    fig.update_layout(
        updatemenus=[dict(
            type="dropdown",
            direction="down",
            x=0.02, y=0.98,
            xanchor="left", yanchor="top",
            bgcolor='rgba(255,255,255,0.9)',
            font=dict(size=12),
            buttons=[
                dict(label="\u2588 Categorical View",
                     method="update",
                     args=[{"visible": cat_vis}]),
                dict(label="\u2593 Analytical View",
                     method="update",
                     args=[{"visible": ana_vis}]),
            ],
        )],
        scene=dict(
            xaxis_title='X (m)',
            yaxis_title='Y (m)',
            zaxis_title='Z (m)',
            aspectmode='data',
            bgcolor='#faf7f2',
        ),
        margin=dict(l=0, r=0, t=30, b=0),
        paper_bgcolor='#f5f0e8',
        legend=dict(
            yanchor="top", y=0.90,
            xanchor="right", x=0.98,
            font=dict(size=10),
            bgcolor='rgba(255,255,255,0.8)',
        ),
    )

    return fig


def figure_to_html(fig):
    """Convert Plotly figure to self-contained HTML string."""
    return fig.to_html(
        include_plotlyjs=True,
        full_html=True,
        config={
            'scrollZoom': True,
            'displayModeBar': True,
            'modeBarButtonsToRemove': ['toImage'],
        }
    )


# ---------------------------------------------------------------------------
#  Convenience: build everything from files
# ---------------------------------------------------------------------------

def build_viewer_html(truss_path=None, result_path=None):
    """
    One-call convenience: load data, compute utilization, build figure, return HTML.
    Returns (html_string, elements_data) or raises on error.
    """
    truss_data = load_truss_data(truss_path)
    analysis_results = load_analysis_results(result_path)
    catalog = load_section_catalog(CHS_CSV) if os.path.exists(CHS_CSV) else []
    elements_data = compute_element_data(truss_data, analysis_results, catalog)
    fig = build_plotly_figure(elements_data)
    html = figure_to_html(fig)
    return html, elements_data


# ---------------------------------------------------------------------------
#  Standalone test
# ---------------------------------------------------------------------------

if __name__ == '__main__':
    import webbrowser

    html, elements_data = build_viewer_html()

    max_u = max((e['utilization'] for e in elements_data), default=0)
    total_cost = sum(e['cost_eur'] for e in elements_data)
    print(f"Elements: {len(elements_data)}")
    print(f"Max utilization: {max_u:.3f}")
    print(f"Total cost: EUR {total_cost:.2f}")

    out_path = os.path.join(SCRIPT_DIR, "_truss_3d_view.html")
    with open(out_path, 'w', encoding='utf-8') as f:
        f.write(html)
    print(f"Saved to {out_path}")
    webbrowser.open(f'file:///{out_path}')
