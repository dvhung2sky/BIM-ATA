"""
Script to select a truss in AutoCAD and extract joint coordinates.
Run this script, then select truss elements in AutoCAD with mouse.
Press Enter when selection is complete.
"""

import win32com.client
import math

# Tolerance for comparing coordinates (adjust if needed)
TOLERANCE = 0.5


def points_are_equal(p1, p2, tol=TOLERANCE):
    """Check if two points are equal within tolerance."""
    dist = math.sqrt((p1[0] - p2[0])**2 + (p1[1] - p2[1])**2 + (p1[2] - p2[2])**2)
    return dist < tol


def get_unique_points(points, tol=TOLERANCE):
    """Remove duplicate points within tolerance."""
    unique = []
    for p in points:
        is_duplicate = False
        for u in unique:
            if points_are_equal(p, u, tol):
                is_duplicate = True
                break
        if not is_duplicate:
            unique.append(p)
    return unique


def get_min_coordinates(joints):
    """Get minimum X, Y, Z values from joints."""
    if not joints:
        return 0, 0, 0
    min_x = min(j['position'][0] for j in joints)
    min_y = min(j['position'][1] for j in joints)
    min_z = min(j['position'][2] for j in joints)
    return min_x, min_y, min_z


def to_relative_coordinates(joints, min_x, min_y, min_z):
    """Convert to relative coordinates by subtracting min X, Y, Z."""
    if not joints:
        return joints
    for j in joints:
        x, y, z = j['position']
        j['position'] = (x - min_x, y - min_y, z - min_z)
    return joints


def calculate_length(start, end):
    """Calculate the length of an element."""
    return math.sqrt((end[0] - start[0])**2 + (end[1] - start[1])**2 + (end[2] - start[2])**2)


def compute_load_direction(normal, rotation_deg):
    """Compute WCS unit direction vector for a load block using the Arbitrary Axis Algorithm.

    Convention: at rotation=0 with Normal=(0,0,1), the load points in -Y (downward in 2D XY plane).
    Verified:
      Normal=(0,0,1), rot=0   -> (0,-1, 0) = -Y  (downward in 2D)
      Normal=(0,0,1), rot=90  -> (1, 0, 0) = +X
      Normal=(0,1,0), rot=0   -> (0, 0,-1) = -Z  (downward in 3D gravity)
      Normal=(0,1,0), rot=90  -> (-1,0, 0) = -X
    """
    nx, ny, nz = normal
    nmag = math.sqrt(nx**2 + ny**2 + nz**2)
    if nmag < 1e-10:
        return (0.0, -1.0, 0.0)
    nx, ny, nz = nx / nmag, ny / nmag, nz / nmag

    # AAA: OCS X-axis (Ax)
    if abs(nx) < 1.0 / 64 and abs(ny) < 1.0 / 64:
        # cross(Y_wcs=(0,1,0), N)
        ax = (nz, 0.0, -nx)
    else:
        # cross(Z_wcs=(0,0,1), N)
        ax = (-ny, nx, 0.0)
    axmag = math.sqrt(ax[0]**2 + ax[1]**2 + ax[2]**2)
    if axmag < 1e-10:
        ax = (1.0, 0.0, 0.0)
    else:
        ax = (ax[0] / axmag, ax[1] / axmag, ax[2] / axmag)

    # OCS Y-axis (Ay) = cross(N, Ax)
    ay = (ny * ax[2] - nz * ax[1],
          nz * ax[0] - nx * ax[2],
          nx * ax[1] - ny * ax[0])
    aymag = math.sqrt(ay[0]**2 + ay[1]**2 + ay[2]**2)
    if aymag > 1e-10:
        ay = (ay[0] / aymag, ay[1] / aymag, ay[2] / aymag)

    # Direction in WCS: sin(theta)*Ax - cos(theta)*Ay
    # At theta=0, Normal=(0,0,1): direction = -Ay = -Y (downward in 2D)
    theta = math.radians(rotation_deg)
    s, c = math.sin(theta), math.cos(theta)
    dx = s * ax[0] - c * ay[0]
    dy = s * ax[1] - c * ay[1]
    dz = s * ax[2] - c * ay[2]
    return (dx, dy, dz)


def direction_label(dx, dy, dz):
    """Convert a WCS direction vector to an axis label like 'X', 'Y', 'Z'."""
    axes = [('X', dx), ('Y', dy), ('Z', dz)]
    name, _ = max(axes, key=lambda a: abs(a[1]))
    return name


def find_closest_joint(point, joints, tol=TOLERANCE):
    """Find the closest joint ID for a given point."""
    min_dist = float('inf')
    closest_id = None
    for joint in joints:
        jpos = joint['position']
        dist = math.sqrt((point[0] - jpos[0])**2 + (point[1] - jpos[1])**2 + (point[2] - jpos[2])**2)
        if dist < min_dist:
            min_dist = dist
            closest_id = joint['id']
    return closest_id if min_dist < tol else None


def get_load_info(selection, min_x, min_y, min_z, joints):
    """Extract load information from 'load' and 'end_load' blocks."""
    loads = []

    for element in selection:
        try:
            # For dynamic blocks, Name may be "*Uxx"; EffectiveName holds the logical name
            block_name = element.Name.lower()
            try:
                effective_name = element.EffectiveName.lower()
            except Exception:
                effective_name = block_name
            matched_name = None
            for candidate in (block_name, effective_name):
                if candidate in ('load', 'end_load'):
                    matched_name = candidate
                    break
            if matched_name is None:
                continue
            block_name = matched_name

            # Get insertion point and normalize
            insertion = tuple(element.InsertionPoint)
            pos_rel = (insertion[0] - min_x, insertion[1] - min_y, insertion[2] - min_z)

            # Get rotation from block Rotation property (same for both load and end_load)
            rotation_rad = element.Rotation
            rotation_deg = math.degrees(rotation_rad)

            # Find corresponding joint ID
            joint_id = find_closest_joint(pos_rel, joints)

            # Get load value from attributes
            load_value = None
            load_value_text = None
            try:
                attrs = element.GetAttributes()
                for attr in attrs:
                    # Try to get load value from attribute
                    try:
                        load_value = float(attr.TextString)
                        load_value_text = attr.TextString.strip()
                        break
                    except ValueError:
                        continue
            except:
                pass

            # Get block scale
            try:
                scale_x = element.XScaleFactor
                scale_y = element.YScaleFactor
                scale_z = element.ZScaleFactor
            except:
                scale_x, scale_y, scale_z = 1.0, 1.0, 1.0

            # Get block Normal vector (OCS orientation for 3D)
            try:
                normal = tuple(element.Normal)
            except:
                normal = (0.0, 0.0, 1.0)

            # Compute WCS load direction and signed force components
            dir_vec = compute_load_direction(normal, rotation_deg)
            dir_str = direction_label(*dir_vec)
            fx = load_value * dir_vec[0] if load_value is not None else None
            fy = load_value * dir_vec[1] if load_value is not None else None
            fz = load_value * dir_vec[2] if load_value is not None else None

            loads.append({
                'type': block_name,
                'position': pos_rel,
                'joint_id': joint_id,
                'rotation': rotation_deg,
                'value': load_value,
                'value_text': load_value_text,
                'scale': (scale_x, scale_y, scale_z),
                'normal': normal,
                'direction': dir_str,
                'fx': fx,
                'fy': fy,
                'fz': fz,
            })

        except Exception:
            continue

    return loads


def get_bc_info(selection, min_x, min_y, min_z, joints):
    """Extract boundary condition information from support blocks."""
    bc_blocks = ['pin_support', '3d_pin', '3dpin_solid', 'z_block', 'roller_support']
    bcs = []

    for element in selection:
        try:
            # Check if it's a block reference
            block_name = element.Name.lower()
            if block_name not in bc_blocks:
                continue

            # Get insertion point and normalize
            insertion = tuple(element.InsertionPoint)
            pos_rel = (insertion[0] - min_x, insertion[1] - min_y, insertion[2] - min_z)

            # Get rotation (in radians, convert to degrees)
            rotation_rad = element.Rotation
            rotation_deg = math.degrees(rotation_rad)
            # Normalize rotation to 0-360
            rotation_deg = rotation_deg % 360

            # Find corresponding joint ID
            joint_id = find_closest_joint(pos_rel, joints)

            # Determine constraint directions based on block type
            if block_name in ('3d_pin', '3dpin_solid'):
                constraints = [1, 2, 3]  # X, Y, Z
            elif block_name == 'pin_support':
                constraints = [1, 2]  # X, Y
            elif block_name == 'z_block':
                constraints = [3]  # Z
            elif block_name == 'roller_support':
                # Check rotation: 0 or 180 -> X(1), 90 or 270 -> Y(2)
                if abs(rotation_deg) < 45 or abs(rotation_deg - 180) < 45 or abs(rotation_deg - 360) < 45:
                    constraints = [1]  # X
                else:
                    constraints = [2]  # Y
            else:
                constraints = []

            bcs.append({
                'type': block_name,
                'position': pos_rel,
                'joint_id': joint_id,
                'rotation': rotation_deg,
                'constraints': constraints
            })

        except AttributeError:
            continue

    return bcs


def get_layer_colors(doc, elements):
    """Get the color index of each layer used by elements."""
    layer_colors = {}
    for elem in elements:
        layer_name = elem['section']
        if layer_name not in layer_colors:
            try:
                layer = doc.Layers.Item(layer_name)
                layer_colors[layer_name] = layer.color
            except:
                layer_colors[layer_name] = 7  # default white
    return layer_colors


def select_objects(doc):
    """Create a selection set and allow user to select objects on screen."""
    # Delete existing selection set if it exists
    try:
        doc.SelectionSets.Item("TrussSelection").Delete()
    except:
        pass

    selection = doc.SelectionSets.Add("TrussSelection")
    print("Select truss elements in AutoCAD, then press Enter...")
    selection.SelectOnScreen()

    return selection


def get_joint_coordinates(selection):
    """Extract joint coordinates from 'Joint' or 'joint3D' blocks with NODE attribute.
    Also returns the original block name used for joints."""
    joints = []
    joint_block_type = 'Joint'  # default

    # Accepted block names for joints
    joint_block_names = ('joint', 'joint3d')
    # Accepted attribute tags for joint ID
    node_attr_tags = ('NODE', 'NUM', 'JOINT', 'NO', 'ID', 'NAME', 'TAG', 'NUMBER')

    for element in selection:
        try:
            # Check if it's a joint block
            block_name = element.Name.lower()
            if block_name not in joint_block_names:
                continue

            # Record the original block name (use the actual case from AutoCAD)
            joint_block_type = element.Name

            # Get insertion point
            insertion = tuple(element.InsertionPoint)

            # Get node ID from attribute
            node_id = None
            try:
                attrs = element.GetAttributes()
                for attr in attrs:
                    if attr.TagString.upper() in node_attr_tags:
                        try:
                            node_id = int(attr.TextString)
                        except ValueError:
                            node_id = attr.TextString
                        break
                # If no matching tag found, use first attribute
                if node_id is None and len(attrs) > 0:
                    try:
                        node_id = int(attrs[0].TextString)
                    except ValueError:
                        node_id = attrs[0].TextString
            except:
                pass

            if node_id is not None:
                joints.append({
                    'id': node_id,
                    'position': insertion
                })

        except AttributeError:
            continue

    return joints, joint_block_type


def get_element_info(selection, min_x, min_y, min_z):
    """Extract element information: start/end coords, length, section."""
    elements = []

    for element in selection:
        try:
            start_point = tuple(element.StartPoint)
            end_point = tuple(element.EndPoint)
            section = element.Layer

            # Normalize coordinates
            start_rel = (start_point[0] - min_x, start_point[1] - min_y, start_point[2] - min_z)
            end_rel = (end_point[0] - min_x, end_point[1] - min_y, end_point[2] - min_z)

            # Calculate length
            length = calculate_length(start_rel, end_rel)

            elements.append({
                'start': start_rel,
                'end': end_rel,
                'length': length,
                'section': section
            })
        except AttributeError:
            continue

    return elements


def export_to_file(joints, elements, loads, bcs, filename="truss.txt", joint_block_type="Joint", layer_colors=None):
    """Export joint coordinates, element info, load info, and BC info to a text file."""
    if layer_colors is None:
        layer_colors = {}
    with open(filename, "w") as f:
        # Drawing info (block types and scales for redrawing)
        f.write("=" * 60 + "\n")
        f.write("DRAWING INFO\n")
        f.write("=" * 60 + "\n")
        f.write(f"joint_block={joint_block_type}\n")
        for layer_name, color_idx in layer_colors.items():
            f.write(f"layer_color={layer_name},{color_idx}\n")
        f.write("=" * 60 + "\n\n")

        # Joint coordinates
        f.write("=" * 60 + "\n")
        f.write("TRUSS JOINT COORDINATES\n")
        f.write("=" * 60 + "\n")
        f.write(f"{'Node ID':<8} {'X':>12} {'Y':>12} {'Z':>12}\n")
        f.write("-" * 60 + "\n")

        for joint in joints:
            x, y, z = joint['position']
            f.write(f"{joint['id']:<8} {x:>12.4f} {y:>12.4f} {z:>12.4f}\n")

        f.write("=" * 60 + "\n\n")

        # Element information
        f.write("=" * 100 + "\n")
        f.write("TRUSS ELEMENT INFORMATION\n")
        f.write("=" * 100 + "\n")
        f.write(f"{'Elem':<6} {'Start X':>10} {'Start Y':>10} {'Start Z':>10} {'End X':>10} {'End Y':>10} {'End Z':>10} {'Length':>10} {'Section':<15}\n")
        f.write("-" * 100 + "\n")

        for i, elem in enumerate(elements, 1):
            sx, sy, sz = elem['start']
            ex, ey, ez = elem['end']
            f.write(f"{i:<6} {sx:>10.4f} {sy:>10.4f} {sz:>10.4f} {ex:>10.4f} {ey:>10.4f} {ez:>10.4f} {elem['length']:>10.4f} {elem['section']:<15}\n")

        f.write("=" * 100 + "\n\n")

        # Load information
        f.write("=" * 110 + "\n")
        f.write("APPLIED LOADS\n")
        f.write("(Fx, Fy, Fz are signed force components in WCS; positive = +axis direction)\n")
        f.write("=" * 110 + "\n")
        f.write(f"{'Load':<6} {'Type':<10} {'Value':>10} {'Joint ID':>10} {'Dir':>5} {'Fx (kN)':>12} {'Fy (kN)':>12} {'Fz (kN)':>12} {'X':>12} {'Y':>12} {'Z':>12}\n")
        f.write("-" * 110 + "\n")

        for i, load in enumerate(loads, 1):
            px, py, pz = load['position']
            value_str = load.get('value_text') or (f"{load['value']:.4f}" if load['value'] is not None else "N/A")
            joint_str = str(load['joint_id']) if load['joint_id'] is not None else "N/A"
            dir_str = load.get('direction', 'N/A')
            fx = load.get('fx')
            fy = load.get('fy')
            fz = load.get('fz')
            fx_str = f"{fx:>12.4f}" if fx is not None else f"{'N/A':>12}"
            fy_str = f"{fy:>12.4f}" if fy is not None else f"{'N/A':>12}"
            fz_str = f"{fz:>12.4f}" if fz is not None else f"{'N/A':>12}"
            f.write(f"{i:<6} {load['type']:<10} {value_str:>10} {joint_str:>10} {dir_str:>5} {fx_str} {fy_str} {fz_str} {px:>12.4f} {py:>12.4f} {pz:>12.4f}\n")

        f.write("=" * 110 + "\n\n")

        # Boundary condition information
        f.write("=" * 100 + "\n")
        f.write("BOUNDARY CONDITIONS\n")
        f.write("(Constraints: 1=X, 2=Y, 3=Z)\n")
        f.write("=" * 100 + "\n")
        f.write(f"{'BC':<6} {'Type':<15} {'X':>12} {'Y':>12} {'Z':>12} {'Joint ID':>10} {'Constraints':<15}\n")
        f.write("-" * 100 + "\n")

        for i, bc in enumerate(bcs, 1):
            px, py, pz = bc['position']
            joint_str = str(bc['joint_id']) if bc['joint_id'] is not None else "N/A"
            constraints_str = ','.join(str(c) for c in bc['constraints'])
            f.write(f"{i:<6} {bc['type']:<15} {px:>12.4f} {py:>12.4f} {pz:>12.4f} {joint_str:>10} {constraints_str:<15}\n")

        f.write("=" * 100 + "\n")

    print(f"\nResults exported to: {filename}")


def main():
    # Connect to AutoCAD
    print("Connecting to AutoCAD...")
    try:
        acad = win32com.client.Dispatch("AutoCAD.Application")
        doc = acad.ActiveDocument
        if doc is None:
            print("Error: No document is open in AutoCAD.")
            return None
        print("Connected to AutoCAD")
    except Exception as e:
        print(f"Error connecting to AutoCAD: {e}")
        print("Make sure AutoCAD is running with a document open.")
        return None

    # Select objects
    selection = select_objects(doc)
    print(f"\nSelected {len(selection)} elements")

    # Get joint coordinates
    joints, joint_block_type = get_joint_coordinates(selection)
    min_x, min_y, min_z = get_min_coordinates(joints)
    joints = to_relative_coordinates(joints, min_x, min_y, min_z)
    print(f"Found {len(joints)} unique joints (block type: {joint_block_type})")

    # Get element information
    elements = get_element_info(selection, min_x, min_y, min_z)
    print(f"Found {len(elements)} elements")

    # Get layer colors from original drawing
    layer_colors = get_layer_colors(doc, elements)

    # Get load information
    loads = get_load_info(selection, min_x, min_y, min_z, joints)
    print(f"Found {len(loads)} loads")

    # Get boundary condition information
    bcs = get_bc_info(selection, min_x, min_y, min_z, joints)
    print(f"Found {len(bcs)} boundary conditions")

    # Export to file
    export_to_file(joints, elements, loads, bcs, "truss.txt", joint_block_type, layer_colors)

    return joints, elements, loads, bcs


if __name__ == "__main__":
    result = main()
    if result:
        joints, elements, loads, bcs = result
