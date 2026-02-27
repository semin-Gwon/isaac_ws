# Robot visualization

import argparse
import sys
import os
import time

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

# Try to import unitree_go message
try:
    from unitree_go.msg import LowState
except ImportError:
    carb.log_error("Could not import unitree_go.msg.LowState. Make sure you sourced '~/isaac_ws/install/setup.bash'!")
    # Dummy class for safety
    class LowState:
        pass

import numpy as np

class Go2Visualizer(Node):
    def __init__(self, articulation):
        super().__init__('go2_visualizer')
        self.articulation = articulation
        self.joint_positions = np.zeros(12)
        self._joint_cb_count = 0
        self._odom_cb_count = 0
        self._last_joint_rx_time = 0.0
        self._last_odom_rx_time = 0.0
        
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
        joint_age_str = f"{joint_age:.2f}s ago" if joint_age is not None else "never"
        odom_age_str = f"{odom_age:.2f}s ago" if odom_age is not None else "never"
        print(
            f"[DIAG] /lf/lowstate callbacks={self._joint_cb_count} last_rx={joint_age_str} | "
            f"/utlidar/robot_odom callbacks={self._odom_cb_count} last_rx={odom_age_str}"
        )

    def update_robot(self):
        # Update Joints
        self.articulation.set_joint_positions(self.joint_positions, joint_indices=self.get_joint_indices())
        
        # Update Base Pose (Odometry)
        self.articulation.set_world_pose(self.base_pos, self.base_ori)

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
    visualizer = Go2Visualizer(go2_robot)
    
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
