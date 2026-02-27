
import os
import struct
import numpy as np
import omni.usd
from pxr import Usd, UsdGeom, Sdf, Gf

def import_rtabmap_ply():
    # Configuration
    ply_file_path = '/home/jnu/.ros/rtabmap_cloud.ply'
    prim_path = '/World/rtabmap_cloud'
    
    if not os.path.exists(ply_file_path):
        print(f"[Error] File not found: {ply_file_path}")
        return

    print(f"[Info] Starting import of {ply_file_path}...")
    
    # 1. Parse Header
    # We anticipate the header format from previous analysis:
    # element vertex N
    # property float x, y, z
    # property uchar red, green, blue
    # property float nx, ny, nz, curvature
    
    vertex_count = 0
    is_binary = False
    header_byte_size = 0
    
    with open(ply_file_path, 'rb') as f:
        while True:
            line = f.readline()
            header_byte_size += len(line)
            line_str = line.decode('ascii', errors='ignore').strip()
            
            if line_str == 'end_header':
                break
            if line_str.startswith('format binary'):
                is_binary = True
            if line_str.startswith('element vertex'):
                vertex_count = int(line_str.split()[-1])
    
    if not is_binary:
        print("[Error] This script only supports binary PLY files.")
        return
        
    print(f"[Info] Header parsed. Vertices: {vertex_count}")

    # 2. Read Data using Numpy
    # The layout is: 3 floats (pos), 3 uchars (color), 4 floats (normal+curv)
    # Total bytes = 12 + 3 + 16 = 31 bytes per vertex.
    # We define a custom structured dtype.
    
    dt = np.dtype([
        ('x', '<f4'), ('y', '<f4'), ('z', '<f4'),
        ('red', 'u1'), ('green', 'u1'), ('blue', 'u1'),
        ('nx', '<f4'), ('ny', '<f4'), ('nz', '<f4'), ('curvature', '<f4')
    ])
    
    # Read the data section
    # We seek to the end of header first? No, we just read from where f left off?
    # Re-opening to be safe and seek is better/cleaner or just read rest after header loop.
    
    with open(ply_file_path, 'rb') as f:
        f.seek(header_byte_size)
        raw_data = f.read()
    
    # Expected size check
    expected_size = vertex_count * 31
    if len(raw_data) != expected_size:
        print(f"[Warning] file size mismatch? Read {len(raw_data)}, expected {expected_size}. Attempting parse anyway.")
        # Truncate or pad if needed, but 'frombuffer' works on exact matches usually or count provided.
    
    try:
        data = np.frombuffer(raw_data, dtype=dt, count=vertex_count)
    except Exception as e:
        print(f"[Error] Failed to parse binary data: {e}")
        return

    # Extract positions and colors
    # Stack them into (N, 3) arrays
    points = np.zeros((vertex_count, 3), dtype=np.float32)
    points[:, 0] = data['x']
    points[:, 1] = data['y']
    points[:, 2] = data['z']
    
    colors = np.zeros((vertex_count, 3), dtype=np.float32)
    colors[:, 0] = data['red']
    colors[:, 1] = data['green']
    colors[:, 2] = data['blue']
    colors /= 255.0 # Normalize 0-1
    
    # 3. Create USD Prim
    ctx = omni.usd.get_context()
    stage = ctx.get_stage()
    if not stage:
        print("[Error] No active stage found in Isaac Sim.")
        return
        
    points_prim = UsdGeom.Points.Define(stage, prim_path)
    
    # Set attributes
    points_prim.CreatePointsAttr(points)
    points_prim.CreateWidthsAttr([0.02] * vertex_count)
    
    # Set Colors (primvars)
    primvar_api = UsdGeom.PrimvarsAPI(points_prim)
    color_var = primvar_api.CreatePrimvar("displayColor", Sdf.ValueTypeNames.Color3fArray)
    color_var.Set(colors)
    color_var.SetInterpolation("vertex")
    
    print(f"[Success] Imported {vertex_count} points to {prim_path}")

# Calculate execution
import_rtabmap_ply()
