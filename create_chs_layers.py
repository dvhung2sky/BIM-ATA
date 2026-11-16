import csv
import os
import win32com.client

def create_autocad_layers_with_colors(csv_path):
    """
    Creates AutoCAD layers from CHS section IDs in a CSV file.
    Assigns colors based on the tube diameter for easy visual identification.
    Requires AutoCAD to be open with an active document.
    """
    # Connect to currently running AutoCAD instance
    acad = win32com.client.Dispatch("AutoCAD.Application")
    doc = acad.ActiveDocument
    layers = doc.Layers

    # Color mapping by diameter range (AutoCAD color index 1-255)
    def get_color_by_diameter(diameter):
        if diameter < 60: return 1      # Red
        elif diameter < 100: return 2   # Yellow
        elif diameter < 150: return 3   # Green
        elif diameter < 200: return 4   # Cyan
        elif diameter < 300: return 5   # Blue
        elif diameter < 400: return 6   # Magenta
        else: return 7                  # White

    created_count = 0
    with open(csv_path, 'r', encoding='utf-8') as file:
        reader = csv.DictReader(file)
        for row in reader:
            layer_id = row['ID']
            if layer_id.startswith('CHS_'):
                try:
                    diameter = float(row['d'])
                    layer = layers.Add(layer_id)
                    layer.color = get_color_by_diameter(diameter)
                    created_count += 1
                    print(f"Created: {layer_id} (d={diameter}mm, color={layer.color})")
                except Exception as e:
                    print(f"Error with '{layer_id}': {e}")

    print(f"\nTotal layers created: {created_count}")

if __name__ == "__main__":
    csv_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "CHS_eurocode.csv")
    create_autocad_layers_with_colors(csv_file)
