# Robot visualization

import argparse
import sys
import os
import time
from pathlib import Path

# Isaac Sim ROS2 bridge의 rclpy 경로 추가 (Humble)
ros2_bridge_humble = (
    "/home/jnu/anaconda3/envs/isaaclab/lib/python3.11/site-packages/"
    "isaacsim/exts/isaacsim.ros2.bridge/humble/rclpy"
)
if os.path.exists(ros2_bridge_humble) and ros2_bridge_humble not in sys.path:
    sys.path.insert(0, ros2_bridge_humble)

# [NEW] Use AppLauncher from Isaac Lab to ensure extensions are loaded correctly
try:
    from isaaclab.app import AppLauncher
except ImportError:
    print("Error: 'isaaclab' not found. Make sure you are in the 'isaaclab' conda environment.")
    print("And ensure you have installed Isaac Lab (pip install -e source/isaaclab).")
    sys.exit(1)

# Argument Parser for AppLauncher
parser = argparse.ArgumentParser(description="Visualize Go2 Robot with ROS 2")
# AppLauncher arguments (headless, etc.)
AppLauncher.add_app_launcher_args(parser)
args_cli = parser.parse_args()

# Force enable omni.isaac.core to fix imports (deprecated but needed for legacy code)
# We need to append this to the kit args string if possible, or usually AppLauncher handles sys.argv
# But since we already parsed args, we might need to rely on the fact that AppLauncher launches kit with accumulated args.
# A cleaner way is to use enable_extension AFTER launch if the module path update happens dynamically,
# BUT for python module imports to work at top level, it needs to be enabled at startup.
# Actually, AppLauncher constructs the command line to launch kit? No, SimulationApp does.
# SimulationApp reads sys.argv. So let's append to sys.argv if not present.
if "--enable" not in sys.argv or "omni.isaac.core" not in sys.argv:
    sys.argv.append("--enable")
    sys.argv.append("omni.isaac.core")

# Launch Isaac Sim app
app_launcher = AppLauncher(args_cli)
simulation_app = app_launcher.app

# Now import the rest (must be after app launch)
import carb
import omni.graph.core as og
from omni.isaac.core import World
from omni.isaac.core.utils.extensions import enable_extension
from omni.isaac.core.robots import Robot
from omni.isaac.core.utils.stage import add_reference_to_stage
from omni.isaac.core.articulations import Articulation

# [NEW] Enable ROS 2 Bridge (Critical for message types)
enable_extension("omni.isaac.ros2_bridge")




# ROS 2 imports
import rclpy
from rclpy.node import Node
from rclpy.qos import QoSProfile, ReliabilityPolicy, HistoryPolicy
from nav_msgs.msg import Odometry
from sensor_msgs.msg import Image

# Try to import unitree_go message
try:
    from unitree_go.msg import LowState
except ImportError:
    carb.log_error("Could not import unitree_go.msg.LowState. Make sure you sourced '~/isaac_ws/install/setup.bash'!")
    # Dummy class for safety
    class LowState:
        pass

import numpy as np
import cv2
from pxr import UsdGeom, UsdShade, Sdf, Gf, Vt

try:
    from PIL import Image as PILImage
except Exception:
    PILImage = None


def quat_rotate_wxyz(q_wxyz, v_xyz):
    """Rotate vector v by quaternion q (w, x, y, z)."""
    w, x, y, z = q_wxyz
    qv = np.array([x, y, z], dtype=np.float64)
    v = np.array(v_xyz, dtype=np.float64)
    t = 2.0 * np.cross(qv, v)
    return v + w * t + np.cross(qv, t)


class VirtualCameraScreen:
    def __init__(self, stage, screen_prim_path="/World/Go2CameraScreen"):
        self.stage = stage
        self.screen_prim_path = screen_prim_path
        self.mat_path = "/World/Materials/Go2CameraScreenMat"
        self.texture_input = None
        self.frame_toggle = 0
        self._last_texture_write = 0.0
        self._write_period_sec = 0.10  # 10Hz texture update
        self.texture_files = [
            Path("/tmp/go2_camera_screen_a.jpg"),
            Path("/tmp/go2_camera_screen_b.jpg"),
        ]
        self._create_screen_mesh_with_material()

    def _create_screen_mesh_with_material(self):
        # Screen anchor transform
        xform = UsdGeom.Xform.Define(self.stage, self.screen_prim_path)
        xform_prim = xform.GetPrim()
        if not xform.AddTranslateOp():
            pass
        if not xform.AddOrientOp():
            pass
        if not xform.AddScaleOp():
            pass

        # Quad mesh (YZ plane), normal toward +X
        mesh_path = f"{self.screen_prim_path}/ScreenMesh"
        mesh = UsdGeom.Mesh.Define(self.stage, mesh_path)
        mesh.CreatePointsAttr(
            [
                Gf.Vec3f(0.0, -0.28, -0.17),
                Gf.Vec3f(0.0, 0.28, -0.17),
                Gf.Vec3f(0.0, 0.28, 0.17),
                Gf.Vec3f(0.0, -0.28, 0.17),
            ]
        )
        mesh.CreateFaceVertexCountsAttr([4])
        mesh.CreateFaceVertexIndicesAttr([0, 1, 2, 3])
        mesh.CreateSubdivisionSchemeAttr("none")
        UsdGeom.Imageable(mesh.GetPrim()).MakeVisible()

        primvars_api = UsdGeom.PrimvarsAPI(mesh)
        st = primvars_api.CreatePrimvar(
            "st", Sdf.ValueTypeNames.TexCoord2fArray, UsdGeom.Tokens.varying
        )
        st.Set(
            Vt.Vec2fArray(
                [
                    Gf.Vec2f(0.0, 0.0),
                    Gf.Vec2f(1.0, 0.0),
                    Gf.Vec2f(1.0, 1.0),
                    Gf.Vec2f(0.0, 1.0),
                ]
            )
        )

        # Material with texture
        material = UsdShade.Material.Define(self.stage, self.mat_path)

        pbr_shader = UsdShade.Shader.Define(self.stage, f"{self.mat_path}/PreviewSurface")
        pbr_shader.CreateIdAttr("UsdPreviewSurface")
        pbr_shader.CreateInput("roughness", Sdf.ValueTypeNames.Float).Set(0.2)
        pbr_shader.CreateInput("metallic", Sdf.ValueTypeNames.Float).Set(0.0)
        # Make the screen look brighter regardless of scene lighting.
        pbr_shader.CreateInput("emissiveColor", Sdf.ValueTypeNames.Color3f).Set(Gf.Vec3f(0.35, 0.35, 0.35))

        st_reader = UsdShade.Shader.Define(self.stage, f"{self.mat_path}/PrimvarReader")
        st_reader.CreateIdAttr("UsdPrimvarReader_float2")
        st_reader.CreateInput("varname", Sdf.ValueTypeNames.Token).Set("st")

        tex_shader = UsdShade.Shader.Define(self.stage, f"{self.mat_path}/DiffuseTexture")
        tex_shader.CreateIdAttr("UsdUVTexture")
        self.texture_input = tex_shader.CreateInput("file", Sdf.ValueTypeNames.Asset)
        self.texture_input.Set(Sdf.AssetPath(str(self.texture_files[0])))
        tex_shader.CreateInput("sourceColorSpace", Sdf.ValueTypeNames.Token).Set("sRGB")
        tex_shader.CreateInput("st", Sdf.ValueTypeNames.Float2).ConnectToSource(st_reader.ConnectableAPI(), "result")

        pbr_shader.CreateInput("diffuseColor", Sdf.ValueTypeNames.Color3f).ConnectToSource(
            tex_shader.ConnectableAPI(), "rgb"
        )
        material.CreateSurfaceOutput().ConnectToSource(pbr_shader.ConnectableAPI(), "surface")

        UsdShade.MaterialBindingAPI(mesh.GetPrim()).Bind(material)

    def update_pose(self, base_pos, base_ori_wxyz):
        xform = UsdGeom.Xform(self.stage.GetPrimAtPath(self.screen_prim_path))
        if not xform:
            return
        # Robot local forward(+X) 0.5m, slight up offset
        local_offset = np.array([0.5, 0.0, 0.25], dtype=np.float64)
        world_offset = quat_rotate_wxyz(base_ori_wxyz, local_offset)
        screen_pos = np.array(base_pos, dtype=np.float64) + world_offset
        screen_ori = np.array(base_ori_wxyz, dtype=np.float64)

        xform_ops = xform.GetOrderedXformOps()
        if len(xform_ops) < 3:
            return
        xform_ops[0].Set(Gf.Vec3f(float(screen_pos[0]), float(screen_pos[1]), float(screen_pos[2])))
        xform_ops[1].Set(
            Gf.Quatf(
                float(screen_ori[0]),
                Gf.Vec3f(float(screen_ori[1]), float(screen_ori[2]), float(screen_ori[3])),
            )
        )
        xform_ops[2].Set(Gf.Vec3f(1.0, 1.0, 1.0))

    def update_texture(self, rgb_image):
        if self.texture_input is None or rgb_image is None:
            return
        now = time.time()
        if now - self._last_texture_write < self._write_period_sec:
            return
        self._last_texture_write = now

        self.frame_toggle = 1 - self.frame_toggle
        out_path = self.texture_files[self.frame_toggle]
        tmp_path = out_path.with_suffix(out_path.suffix + ".tmp")
        if PILImage is not None:
            PILImage.fromarray(rgb_image, mode="RGB").save(str(tmp_path), format="JPEG", quality=92)
        else:
            # Fallback when Pillow is not installed in Isaac Sim env.
            bgr_image = rgb_image[:, :, ::-1]
            cv2.imwrite(str(tmp_path), bgr_image, [int(cv2.IMWRITE_JPEG_QUALITY), 92])
        os.replace(str(tmp_path), str(out_path))
        # Alternate file path to force texture refresh
        self.texture_input.Set(Sdf.AssetPath(str(out_path)))

class Go2Visualizer(Node):
    def __init__(self, articulation, stage):
        super().__init__('go2_visualizer')
        self.articulation = articulation
        self.virtual_screen = VirtualCameraScreen(stage=stage)
        self.joint_positions = np.zeros(12)
        self._joint_cb_count = 0
        self._odom_cb_count = 0
        self._img_cb_count = 0
        self._last_joint_rx_time = 0.0
        self._last_odom_rx_time = 0.0
        self._last_img_rx_time = 0.0
        self.latest_rgb_image = None
        
        # Base pose (Position: x,y,z / Orientation: w,x,y,z)
        self.base_pos = np.array([0.0, 0.0, 0.0])
        self.base_ori = np.array([1.0, 0.0, 0.0, 0.0]) # w, x, y, z
        
        # QoS setup
        qos = QoSProfile(
            reliability=ReliabilityPolicy.BEST_EFFORT,
            history=HistoryPolicy.KEEP_LAST,
            depth=1
        )

        # Joint State Subscription
        self.sub_joint = self.create_subscription(
            LowState,
            '/lf/lowstate',
            self.listener_callback,
            qos
        )
        
        # Odometry Subscription
        self.sub_odom = self.create_subscription(
            Odometry,
            '/utlidar/robot_odom',
            self.odom_callback,
            qos
        )

        # Camera image subscription for virtual screen
        self.sub_color_sync = self.create_subscription(
            Image,
            '/my_go2/color/image_raw_sync',
            self.image_callback,
            qos
        )
        self.sub_color_raw = self.create_subscription(
            Image,
            '/my_go2/color/image_raw',
            self.image_callback,
            qos
        )
        
        self.joint_names = [
            "FR_hip_joint", "FR_thigh_joint", "FR_calf_joint",
            "FL_hip_joint", "FL_thigh_joint", "FL_calf_joint",
            "RR_hip_joint", "RR_thigh_joint", "RR_calf_joint",
            "RL_hip_joint", "RL_thigh_joint", "RL_calf_joint"
        ]
        self.create_timer(2.0, self.log_subscription_diagnostics)
        
    def listener_callback(self, msg):
        self._joint_cb_count += 1
        self._last_joint_rx_time = time.time()
        # [DEBUG] Check if callback is triggered
        if not hasattr(self, '_debug_counter'):
            self._debug_counter = 0
        self._debug_counter += 1
        
        if self._debug_counter % 60 == 0: # Print once every ~1-2 seconds
            print(f"[DEBUG] Received /lf/lowstate! Seq: {self._debug_counter}")

        if hasattr(msg, 'motor_state'):
            ms = msg.motor_state
            for i in range(12):
                self.joint_positions[i] = ms[i].q

    def odom_callback(self, msg):
        self._odom_cb_count += 1
        self._last_odom_rx_time = time.time()
        # Extract position
        p = msg.pose.pose.position
        self.base_pos = np.array([p.x, p.y, p.z])
        
        # Extract orientation (Quaternion)
        q = msg.pose.pose.orientation
        # Isaac Sim uses [w, x, y, z] convention for quaternions
        self.base_ori = np.array([q.w, q.x, q.y, q.z])

    def log_subscription_diagnostics(self):
        now = time.time()
        joint_age = (now - self._last_joint_rx_time) if self._last_joint_rx_time > 0.0 else None
        odom_age = (now - self._last_odom_rx_time) if self._last_odom_rx_time > 0.0 else None
        img_age = (now - self._last_img_rx_time) if self._last_img_rx_time > 0.0 else None
        joint_age_str = f"{joint_age:.2f}s ago" if joint_age is not None else "never"
        odom_age_str = f"{odom_age:.2f}s ago" if odom_age is not None else "never"
        img_age_str = f"{img_age:.2f}s ago" if img_age is not None else "never"
        print(
            f"[DIAG] /lf/lowstate callbacks={self._joint_cb_count} last_rx={joint_age_str} | "
            f"/utlidar/robot_odom callbacks={self._odom_cb_count} last_rx={odom_age_str} | "
            f"/my_go2/color/image_raw_sync callbacks={self._img_cb_count} last_rx={img_age_str}"
        )
        if PILImage is None:
            print("[DIAG] Pillow not found, using OpenCV fallback for screen texture writes.")

    def image_callback(self, msg):
        self._img_cb_count += 1
        self._last_img_rx_time = time.time()
        try:
            h, w = int(msg.height), int(msg.width)
            if h <= 0 or w <= 0:
                return
            arr = np.frombuffer(msg.data, dtype=np.uint8)
            enc = (msg.encoding or "").lower()
            if enc == "rgb8":
                rgb = arr.reshape((h, w, 3))
            elif enc == "bgr8":
                bgr = arr.reshape((h, w, 3))
                rgb = bgr[:, :, ::-1]
            elif enc == "rgba8":
                rgba = arr.reshape((h, w, 4))
                rgb = rgba[:, :, :3]
            elif enc == "bgra8":
                bgra = arr.reshape((h, w, 4))
                rgb = bgra[:, :, :3][:, :, ::-1]
            elif enc == "mono8":
                mono = arr.reshape((h, w, 1))
                rgb = np.repeat(mono, 3, axis=2)
            else:
                # Unsupported encoding for quick texture mapping
                return
            self.latest_rgb_image = rgb.copy()
        except Exception as e:
            if not hasattr(self, "_img_decode_warned"):
                self._img_decode_warned = True
                carb.log_warn(f"Image decode failed once: {e}")

    def update_robot(self):
        # Update Joints
        self.articulation.set_joint_positions(self.joint_positions, joint_indices=self.get_joint_indices())
        
        # Update Base Pose (Odometry)
        self.articulation.set_world_pose(self.base_pos, self.base_ori)
        self.virtual_screen.update_pose(self.base_pos, self.base_ori)
        self.virtual_screen.update_texture(self.latest_rgb_image)

    def get_joint_indices(self):
        if not hasattr(self, '_joint_indices'):
            all_joints = self.articulation.dof_names
            indices = []
            for name in self.joint_names:
                try:
                    idx = all_joints.index(name)
                    indices.append(idx)
                except ValueError:
                    carb.log_warn(f"Joint {name} not found in Articulation")
            self._joint_indices = indices
        return self._joint_indices

def main():
    
    # 1. Load World
    world = World(stage_units_in_meters=1.0)
    world.scene.add_default_ground_plane()
    
    # 2. Add Robot URDF -> 본인 경로에 맞게 변경 필요
    urdf_path = "/home/jnu/go2_ws/src/go2_description/urdf/go2_description.urdf"
    
    # Enable URDF importer explicitly (AppLauncher should help find it)
    enable_extension("isaacsim.asset.importer.urdf")
    import omni.kit.commands
    from isaacsim.asset.importer.urdf import _urdf
    
    # Configure Import
    import_config = _urdf.ImportConfig()
    import_config.merge_fixed_joints = False
    import_config.fix_base = False
    import_config.make_default_prim = True
    import_config.self_collision = False
    import_config.create_physics_scene = True
    import_config.import_inertia_tensor = False
    import_config.distance_scale = 1.0
    import_config.density = 0.0
    
    dest_path = "/tmp/go2.usd"
    prim_path = "/World/Go2"
    
    # Run the import command (converts URDF to USD)
    omni.kit.commands.execute(
        "URDFParseAndImportFile",
        urdf_path=urdf_path,
        import_config=import_config,
        dest_path=dest_path
    )
    
    # Add reference to the stage
    add_reference_to_stage(usd_path=dest_path, prim_path=prim_path)

    # Wrap in Articulation
    go2_robot = Articulation(prim_path=prim_path, name="go2")
    world.scene.add(go2_robot)
    
    # 3. Setup ROS 2
    # [NEW] Set CycloneDDS URI if not already set
    if 'CYCLONEDDS_URI' not in os.environ:
        os.environ['CYCLONEDDS_URI'] = 'file:///home/jnu/isaac_ws/cyclonedds.xml'

    rclpy.init()
    visualizer = Go2Visualizer(go2_robot, world.stage)
    
    world.reset()
    
    # 4. Loop
    while simulation_app.is_running():
        world.step(render=True)
        rclpy.spin_once(visualizer, timeout_sec=0.0)
        
        if go2_robot.handles_initialized:
            visualizer.update_robot()
            
    # Cleanup
    visualizer.destroy_node()
    rclpy.shutdown()
    simulation_app.close()

if __name__ == "__main__":
    main()
