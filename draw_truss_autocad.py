"""
Draw Truss in AutoCAD from truss.txt
The starting point is selected by clicking mouse in AutoCAD
Uses win32com for full control over block attributes
"""

import math
import os
import win32com.client
from win32com.client import VARIANT
import pythoncom


def APoint(x, y, z):
    """Create a 3D point as a VARIANT array for AutoCAD."""
    return VARIANT(pythoncom.VT_ARRAY | pythoncom.VT_R8, (x, y, z))


def ocs_axes(normal):
    """Compute OCS X and Y axes using AutoCAD's Arbitrary Axis Algorithm (AAA)."""
    nx, ny, nz = normal
    threshold = 1.0 / 64.0
    if abs(nx) < threshold and abs(ny) < threshold:
        ref = (0.0, 1.0, 0.0)
    else:
        ref = (0.0, 0.0, 1.0)
    # ax = cross(ref, normal)
    ax = (ref[1]*nz - ref[2]*ny, ref[2]*nx - ref[0]*nz, ref[0]*ny - ref[1]*nx)
    ax_len = math.sqrt(ax[0]**2 + ax[1]**2 + ax[2]**2)
    ax = (ax[0]/ax_len, ax[1]/ax_len, ax[2]/ax_len)
    # ay = cross(normal, ax)
    ay = (ny*ax[2] - nz*ax[1], nz*ax[0] - nx*ax[2], nx*ax[1] - ny*ax[0])
    ay_len = math.sqrt(ay[0]**2 + ay[1]**2 + ay[2]**2)
    ay = (ay[0]/ay_len, ay[1]/ay_len, ay[2]/ay_len)
    return ax, ay


def parse_truss_file(filename):
    """Parse the truss.txt file and extract element data."""
    elements = []
    joints = {}
    loads = []
    boundary_conditions = []
    drawing_info = {'joint_block': 'Joint'}

    with open(filename, 'r') as f:
        content = f.read()

    lines = content.split('\n')

    # Parse sections
    current_section = None

    for line in lines:
        line = line.strip()

        # Detect section headers
        if 'DRAWING INFO' in line:
            current_section = 'drawing_info'
            continue
        elif 'TRUSS JOINT COORDINATES' in line:
            current_section = 'joints'
            continue
        elif 'TRUSS ELEMENT INFORMATION' in line:
            current_section = 'elements'
            continue
        elif 'APPLIED LOADS' in line:
            current_section = 'loads'
            continue
        elif 'BOUNDARY CONDITIONS' in line:
            current_section = 'bc'
            continue

        # Skip header lines and separators
        if line.startswith('=') or line.startswith('-') or not line:
            continue
        if line.startswith('Joint') or line.startswith('Node') or line.startswith('Elem') or line.startswith('Load') or line.startswith('BC'):
            continue
        if '(rotation' in line.lower() or '(constraints' in line.lower():
            continue

        # Parse data based on current section
        if current_section == 'drawing_info':
            if line.startswith('joint_block='):
                drawing_info['joint_block'] = line.split('=', 1)[1].strip()
            elif line.startswith('layer_color='):
                # Format: layer_color=LayerName,ColorIndex
                val = line.split('=', 1)[1].strip()
                parts = val.rsplit(',', 1)
                if len(parts) == 2:
                    try:
                        if 'layer_colors' not in drawing_info:
                            drawing_info['layer_colors'] = {}
                        drawing_info['layer_colors'][parts[0]] = int(parts[1])
                    except ValueError:
                        pass

        elif current_section == 'joints':
            parts = line.split()
            if len(parts) >= 4:
                try:
                    joint_id = int(parts[0])
                    x = float(parts[1])
                    y = float(parts[2])
                    z = float(parts[3])
                    joints[joint_id] = (x, y, z)
                except ValueError:
                    continue

        elif current_section == 'elements':
            parts = line.split()
            if len(parts) >= 8:
                try:
                    elem_id = int(parts[0])
                    start_x = float(parts[1])
                    start_y = float(parts[2])
                    start_z = float(parts[3])
                    end_x = float(parts[4])
                    end_y = float(parts[5])
                    end_z = float(parts[6])
                    length = float(parts[7])
                    section = parts[8] if len(parts) > 8 else "Unknown"

                    elements.append({
                        'id': elem_id,
                        'start': (start_x, start_y, start_z),
                        'end': (end_x, end_y, end_z),
                        'length': length,
                        'section': section
                    })
                except ValueError:
                    continue

        elif current_section == 'loads':
            parts = line.split()
            if len(parts) >= 8:
                try:
                    load_id = int(parts[0])
                    load_type = parts[1]
                    value_text = parts[2]
                    value = float(value_text)
                    x = float(parts[3])
                    y = float(parts[4])
                    z = float(parts[5])
                    joint_id = int(parts[6])
                    rotation = float(parts[7])

                    # Parse scale if available (columns 9, 10, 11)
                    scale_x = float(parts[8]) if len(parts) > 10 else 1.0
                    scale_y = float(parts[9]) if len(parts) > 10 else 1.0
                    scale_z = float(parts[10]) if len(parts) > 10 else 1.0

                    # Parse Normal vector if available (columns 12, 13, 14)
                    normal_x = float(parts[11]) if len(parts) > 13 else 0.0
                    normal_y = float(parts[12]) if len(parts) > 13 else 0.0
                    normal_z = float(parts[13]) if len(parts) > 13 else 1.0

                    loads.append({
                        'id': load_id,
                        'type': load_type,
                        'value': value,
                        'value_text': value_text,
                        'position': (x, y, z),
                        'joint_id': joint_id,
                        'rotation': rotation,
                        'scale': (scale_x, scale_y, scale_z),
                        'normal': (normal_x, normal_y, normal_z)
                    })
                except ValueError:
                    continue

        elif current_section == 'bc':
            parts = line.split()
            if len(parts) >= 6:
                try:
                    bc_id = int(parts[0])
                    bc_type = parts[1]
                    x = float(parts[2])
                    y = float(parts[3])
                    z = float(parts[4])
                    joint_id = int(parts[5])
                    constraints = parts[6] if len(parts) > 6 else ""

                    boundary_conditions.append({
                        'id': bc_id,
                        'type': bc_type,
                        'position': (x, y, z),
                        'joint_id': joint_id,
                        'constraints': constraints
                    })
                except ValueError:
                    continue

    return {
        'joints': joints,
        'elements': elements,
        'loads': loads,
        'boundary_conditions': boundary_conditions,
        'drawing_info': drawing_info
    }


def check_block_exists(doc, block_name):
    """Check if a block definition exists in the drawing."""
    try:
        for block in doc.Blocks:
            if block.Name.lower() == block_name.lower():
                return True
    except:
        pass
    return False


def create_joint_block(doc):
    """Create the Joint block with NODE attribute if it doesn't exist."""
    block_name = "Joint"

    if check_block_exists(doc, block_name):
        print(f"Block '{block_name}' already exists in the drawing.")
        return True

    try:
        # Create a new block definition
        insertion_point = APoint(0, 0, 0)
        block_def = doc.Blocks.Add(insertion_point, block_name)

        # Add a small circle to represent the joint
        center = APoint(0, 0, 0)
        circle = block_def.AddCircle(center, 0.2)
        circle.Color = 2  # Yellow

        # Add cross lines for visibility
        line1 = block_def.AddLine(APoint(-0.3, 0, 0), APoint(0.3, 0, 0))
        line2 = block_def.AddLine(APoint(0, -0.3, 0), APoint(0, 0.3, 0))
        line1.Color = 2
        line2.Color = 2

        # Add NODE attribute definition
        attr_insertion = APoint(0.5, 0, 0)
        attr_def = block_def.AddAttribute(
            0.3,                    # Height
            0,                      # Mode (0 = normal)
            "Enter Node ID",        # Prompt
            attr_insertion,         # Insertion point
            "NODE",                 # Tag
            "0"                     # Default value
        )
        attr_def.Color = 3  # Green

        print(f"Block '{block_name}' created successfully with NODE attribute.")
        return True

    except Exception as e:
        print(f"Error creating Joint block: {e}")
        return False


def create_load_block(doc, block_name):
    """Create load block if it doesn't exist."""
    if check_block_exists(doc, block_name):
        return True

    try:
        insertion_point = APoint(0, 0, 0)
        block_def = doc.Blocks.Add(insertion_point, block_name)

        # Arrow pointing down (for rotation 0)
        arrow_length = 2.0
        block_def.AddLine(APoint(0, 0, 0), APoint(0, arrow_length, 0))
        block_def.AddLine(APoint(0, 0, 0), APoint(-0.3, 0.5, 0))
        block_def.AddLine(APoint(0, 0, 0), APoint(0.3, 0.5, 0))

        # Add value attribute
        attr_insertion = APoint(0.5, 1.0, 0)
        attr_def = block_def.AddAttribute(
            0.3, 0, "Load Value", attr_insertion, "VALUE", "0"
        )
        attr_def.Color = 6  # Magenta

        return True
    except Exception as e:
        print(f"Error creating {block_name} block: {e}")
        return False


def create_support_block(doc, block_name):
    """Create support block if it doesn't exist."""
    if check_block_exists(doc, block_name):
        return True

    try:
        insertion_point = APoint(0, 0, 0)
        block_def = doc.Blocks.Add(insertion_point, block_name)

        if block_name == 'pin_support' or block_name == '3d_pin':
            # Triangle shape for pin
            block_def.AddLine(APoint(0, 0, 0), APoint(-0.5, -0.8, 0))
            block_def.AddLine(APoint(0, 0, 0), APoint(0.5, -0.8, 0))
            block_def.AddLine(APoint(-0.5, -0.8, 0), APoint(0.5, -0.8, 0))
        elif block_name == 'roller_support':
            # Triangle with circle for roller
            block_def.AddLine(APoint(0, 0, 0), APoint(-0.5, -0.6, 0))
            block_def.AddLine(APoint(0, 0, 0), APoint(0.5, -0.6, 0))
            block_def.AddLine(APoint(-0.5, -0.6, 0), APoint(0.5, -0.6, 0))
            block_def.AddCircle(APoint(0, -0.8, 0), 0.2)
        elif block_name == 'z_block':
            # Square for z-block
            block_def.AddLine(APoint(-0.3, -0.3, 0), APoint(0.3, -0.3, 0))
            block_def.AddLine(APoint(0.3, -0.3, 0), APoint(0.3, 0.3, 0))
            block_def.AddLine(APoint(0.3, 0.3, 0), APoint(-0.3, 0.3, 0))
            block_def.AddLine(APoint(-0.3, 0.3, 0), APoint(-0.3, -0.3, 0))

        return True
    except Exception as e:
        print(f"Error creating {block_name} block: {e}")
        return False


def draw_truss_in_autocad(doc, model, truss_data, base_point):
    """Draw the truss in AutoCAD starting from the base point."""

    # Determine which joint block to use
    joint_block_name = truss_data.get('drawing_info', {}).get('joint_block', 'Joint')

    print(f"\nUsing joint block: {joint_block_name}")
    print("Creating block definitions...")

    # Create joint block only if it's the default "Joint" (don't create joint3D — it must already exist)
    if joint_block_name.lower() == 'joint':
        create_joint_block(doc)
    elif not check_block_exists(doc, joint_block_name):
        print(f"WARNING: Block '{joint_block_name}' not found in drawing, falling back to 'Joint'")
        create_joint_block(doc)
        joint_block_name = 'Joint'

    create_load_block(doc, "load")
    create_load_block(doc, "end_load")
    create_support_block(doc, "pin_support")
    create_support_block(doc, "3d_pin")
    create_support_block(doc, "3dpin_solid")
    create_support_block(doc, "roller_support")
    create_support_block(doc, "z_block")

    # Calculate offset from original (0,0,0) to base point
    offset_x = base_point[0]
    offset_y = base_point[1]
    offset_z = base_point[2]

    print("\nDrawing elements...")

    # Draw elements as 3D lines on appropriate layers
    for elem in truss_data['elements']:
        start = elem['start']
        end = elem['end']
        section = elem['section']

        # Apply offset
        start_point = APoint(
            start[0] + offset_x,
            start[1] + offset_y,
            start[2] + offset_z
        )
        end_point = APoint(
            end[0] + offset_x,
            end[1] + offset_y,
            end[2] + offset_z
        )

        # Draw line
        line = model.AddLine(start_point, end_point)

        # Set layer to section name (create layer if needed)
        # Use original layer color from drawing info, fallback to defaults
        layer_colors = truss_data.get('drawing_info', {}).get('layer_colors', {})
        try:
            layer = doc.Layers.Add(section)
            if section in layer_colors:
                layer.Color = layer_colors[section]
            elif 'CHS_323' in section:
                layer.Color = 1  # Red - main chords
            elif 'CHS_273' in section:
                layer.Color = 3  # Green - secondary members
            elif 'CHS_42' in section:
                layer.Color = 5  # Blue - bracing
            else:
                layer.Color = 7  # White - default
        except:
            pass  # Layer already exists

        line.Layer = section
        line.Color = 256  # ByLayer

    print(f"\nDrawing joints as '{joint_block_name}' blocks...")

    # Draw joints using the original block type
    node_attr_tags = ('NODE', 'NUM', 'JOINT', 'NO', 'ID', 'NAME', 'TAG', 'NUMBER')
    for joint_id, coords in truss_data['joints'].items():
        insertion_point = APoint(
            coords[0] + offset_x,
            coords[1] + offset_y,
            coords[2] + offset_z
        )

        try:
            block_ref = model.InsertBlock(
                insertion_point,
                joint_block_name,
                1.0, 1.0, 1.0,  # X, Y, Z scale
                0.0  # Rotation
            )

            # Set the node ID attribute
            if block_ref.HasAttributes:
                attrs = block_ref.GetAttributes()
                attr_set = False
                for attr in attrs:
                    if attr.TagString.upper() in node_attr_tags:
                        attr.TextString = str(joint_id)
                        attr_set = True
                        break
                # Fallback: set first attribute
                if not attr_set and len(attrs) > 0:
                    attrs[0].TextString = str(joint_id)

        except Exception as e:
            print(f"Error inserting joint {joint_id}: {e}")

    print("\nDrawing loads...")

    # Draw load blocks with original scale and Normal vector
    for load in truss_data['loads']:
        pos = load['position']
        rotation = load['rotation']
        value_text = load.get('value_text', str(load['value']))
        load_type = load['type']
        scale_x, scale_y, scale_z = load.get('scale', (1.0, 1.0, 1.0))
        normal = load.get('normal', (0.0, 0.0, 1.0))

        insertion_point = APoint(
            pos[0] + offset_x,
            pos[1] + offset_y,
            pos[2] + offset_z
        )

        try:
            rotation_rad = math.radians(rotation)
            nx, ny, nz = normal
            ix = pos[0] + offset_x
            iy = pos[1] + offset_y
            iz = pos[2] + offset_z

            # Insert block with default orientation
            block_ref = model.InsertBlock(
                insertion_point,
                load_type,
                scale_x, scale_y, scale_z,
                0.0
            )

            # Compute full rotation using AAA + in-plane rotation
            # Target OCS axes from AutoCAD's Arbitrary Axis Algorithm
            ax_t, ay_t = ocs_axes((nx, ny, nz))
            cos_r = math.cos(rotation_rad)
            sin_r = math.sin(rotation_rad)

            # Rotation matrix columns (where default X,Y,Z axes map to)
            c1 = (cos_r*ax_t[0] + sin_r*ay_t[0],
                  cos_r*ax_t[1] + sin_r*ay_t[1],
                  cos_r*ax_t[2] + sin_r*ay_t[2])
            c2 = (-sin_r*ax_t[0] + cos_r*ay_t[0],
                  -sin_r*ax_t[1] + cos_r*ay_t[1],
                  -sin_r*ax_t[2] + cos_r*ay_t[2])
            c3 = (nx, ny, nz)

            # Convert rotation matrix to axis-angle for Rotate3D
            trace_m = c1[0] + c2[1] + c3[2]
            cos_a = max(-1.0, min(1.0, (trace_m - 1.0) / 2.0))
            rot_angle = math.acos(cos_a)

            if rot_angle > 1e-6:
                if abs(rot_angle - math.pi) < 1e-6:
                    # 180° rotation: find axis from (M + I)
                    mi = (c1[0]+1, c1[1], c1[2],
                          c2[0], c2[1]+1, c2[2],
                          c3[0], c3[1], c3[2]+1)
                    col_norms = [
                        mi[0]**2 + mi[3]**2 + mi[6]**2,
                        mi[1]**2 + mi[4]**2 + mi[7]**2,
                        mi[2]**2 + mi[5]**2 + mi[8]**2
                    ]
                    best = col_norms.index(max(col_norms))
                    if best == 0:
                        ar = (mi[0], mi[3], mi[6])
                    elif best == 1:
                        ar = (mi[1], mi[4], mi[7])
                    else:
                        ar = (mi[2], mi[5], mi[8])
                    ar_len = math.sqrt(ar[0]**2 + ar[1]**2 + ar[2]**2)
                    ar = (ar[0]/ar_len, ar[1]/ar_len, ar[2]/ar_len)
                    pt1 = APoint(ix, iy, iz)
                    pt2 = APoint(ix + ar[0], iy + ar[1], iz + ar[2])
                    block_ref.Rotate3D(pt1, pt2, math.pi)
                else:
                    # General case
                    s = 2.0 * math.sin(rot_angle)
                    ar = ((c2[2]-c3[1])/s, (c3[0]-c1[2])/s, (c1[1]-c2[0])/s)
                    pt1 = APoint(ix, iy, iz)
                    pt2 = APoint(ix + ar[0], iy + ar[1], iz + ar[2])
                    block_ref.Rotate3D(pt1, pt2, rot_angle)

            block_ref.Color = 6  # Magenta

            # Set the VALUE attribute using original text
            attrs = block_ref.GetAttributes()
            for attr in attrs:
                if attr.TagString.upper() == "VALUE":
                    attr.TextString = value_text
                    break

        except Exception as e:
            print(f"Error inserting load: {e}")

    print("\nDrawing boundary conditions...")

    # Draw boundary condition blocks
    for bc in truss_data['boundary_conditions']:
        pos = bc['position']
        bc_type = bc['type']

        insertion_point = APoint(
            pos[0] + offset_x,
            pos[1] + offset_y,
            pos[2] + offset_z
        )

        try:
            block_ref = model.InsertBlock(
                insertion_point,
                bc_type,
                1.0, 1.0, 1.0,
                0.0
            )

            # Color based on support type
            if 'pin' in bc_type.lower() or '3d_pin' in bc_type.lower():
                block_ref.Color = 2  # Yellow
            elif 'roller' in bc_type.lower():
                block_ref.Color = 4  # Cyan
            else:
                block_ref.Color = 7  # White

        except Exception as e:
            print(f"Error inserting boundary condition: {e}")

    return True


def connect_to_autocad():
    """Connect to AutoCAD application."""
    print("Connecting to AutoCAD...")
    try:
        acad = win32com.client.Dispatch("AutoCAD.Application")
        doc = acad.ActiveDocument
        if doc is None:
            print("Error: No document is open in AutoCAD.")
            return None, None, None
        model = doc.ModelSpace
        print("Connected to AutoCAD successfully.")
        return acad, doc, model
    except Exception as e:
        print(f"Error connecting to AutoCAD: {e}")
        print("Make sure AutoCAD is running with a document open.")
        return None, None, None


def get_point_from_user(doc):
    """Get a point from user by mouse click in AutoCAD."""

    print("\n=== TRUSS DRAWING ===")
    print("Switch to AutoCAD and click a point to set the truss starting position...")

    # Get point from user
    try:
        point = doc.Utility.GetPoint(Prompt="\nSelect starting point for truss: ")
        return (point[0], point[1], point[2])
    except Exception as e:
        print(f"Error getting point: {e}")
        print("Using default origin (0, 0, 0)")
        return (0.0, 0.0, 0.0)


def main():
    """Main function to draw truss in AutoCAD."""

    # File path
    truss_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "truss.txt")

    print("=" * 60)
    print("TRUSS DRAWING TOOL FOR AUTOCAD")
    print("=" * 60)

    # Connect to AutoCAD first
    acad, doc, model = connect_to_autocad()
    if doc is None:
        return

    # Parse truss file
    print(f"\nReading truss data from: {truss_file}")
    truss_data = parse_truss_file(truss_file)

    print(f"\nTruss data loaded:")
    print(f"  - Joints: {len(truss_data['joints'])}")
    print(f"  - Elements: {len(truss_data['elements'])}")
    print(f"  - Loads: {len(truss_data['loads'])}")
    print(f"  - Boundary conditions: {len(truss_data['boundary_conditions'])}")

    # Get starting point from user in AutoCAD
    print("\nPlease switch to AutoCAD and click a point...")
    print("(The point you click will be the origin of the truss)")

    base_point = get_point_from_user(doc)
    print(f"\nBase point selected: ({base_point[0]:.4f}, {base_point[1]:.4f}, {base_point[2]:.4f})")

    # Draw the truss
    print("\nDrawing truss...")
    draw_truss_in_autocad(doc, model, truss_data, base_point)

    # Zoom to fit
    try:
        acad.ZoomExtents()
    except:
        pass

    print("\n" + "=" * 60)
    print("DRAWING COMPLETE!")
    print("=" * 60)
    print(f"\n{len(truss_data['elements'])} elements drawn.")
    print(f"{len(truss_data['joints'])} joints with NODE attribute.")
    print("\nColor Legend:")
    print("  RED (1)     - Main chords (CHS_323.9x17.5)")
    print("  GREEN (3)   - Secondary members (CHS_273.0x8.0)")
    print("  BLUE (5)    - Bracing (CHS_42.4x3.2)")
    print("  MAGENTA (6) - Load blocks")
    print("  YELLOW (2)  - Pin supports / Joint markers")
    print("  CYAN (4)    - Roller supports")
    print("\nJoint blocks have 'NODE' attribute with the Node ID.")


if __name__ == "__main__":
    main()
