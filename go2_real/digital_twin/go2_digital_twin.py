import argparse
import os
import sys
import time
import threading

import numpy as np

# [DDS 환경 설정] Isaac Sim 앱 실행 전에 반드시 설정
os.environ.setdefault("RMW_IMPLEMENTATION", "rmw_cyclonedds_cpp")
if "CYCLONEDDS_URI" in os.environ:
    print(f"[DDS 설정] 기존 CYCLONEDDS_URI 유지: {os.environ['CYCLONEDDS_URI']}")
else:
    print("[DDS 설정] CYCLONEDDS_URI 미설정 상태로 실행합니다.")
os.environ.setdefault("ROS_DOMAIN_ID", "0")
print(f"[DDS 설정] ROS_DOMAIN_ID={os.environ.get('ROS_DOMAIN_ID', '0')}")

# Isaac Sim ROS2 bridge의 rclpy 경로 추가 (Humble)
ros2_bridge_humble = (
    "/home/jnu/anaconda3/envs/isaaclab/lib/python3.11/site-packages/"
    "isaacsim/exts/isaacsim.ros2.bridge/humble/rclpy"
)
if os.path.exists(ros2_bridge_humble) and ros2_bridge_humble not in sys.path:
    sys.path.insert(0, ros2_bridge_humble)

try:
    from isaaclab.app import AppLauncher
except ImportError:
    print("오류: 'isaaclab'을 찾을 수 없습니다. 'isaaclab' 콘다 환경에 있는지 확인하세요.")
    sys.exit(1)

parser = argparse.ArgumentParser(description="Go2 로봇 ROS 2 디지털 트윈 (Joint/Odom 전용)")
AppLauncher.add_app_launcher_args(parser)
args_cli = parser.parse_args()

if "--enable" not in sys.argv or "omni.isaac.core" not in sys.argv:
    sys.argv.append("--enable")
    sys.argv.append("omni.isaac.core")

app_launcher = AppLauncher(args_cli)
simulation_app = app_launcher.app

import omni
import omni.kit.commands

from omni.isaac.core.articulations import Articulation
from omni.isaac.core.world import World
from omni.isaac.core.utils.extensions import enable_extension
from omni.isaac.core.utils.stage import add_reference_to_stage

import rclpy
from rclpy.node import Node
from rclpy.qos import QoSProfile, ReliabilityPolicy, HistoryPolicy
from rclpy.callback_groups import ReentrantCallbackGroup
from nav_msgs.msg import Odometry
from sensor_msgs.msg import JointState


class Go2Visualizer(Node):
    """ROS 2 관절/오도메트리 상태를 Isaac Sim 로봇에 동기화하는 노드."""

    def __init__(self, articulation: Articulation):
        super().__init__("go2_visualizer")
        self.articulation = articulation

        self.joint_positions = np.zeros(12)
        self.base_pos = np.array([0.0, 0.0, 0.3])
        self.base_ori = np.array([1.0, 0.0, 0.0, 0.0])

        self._subs = []
        self._odom_cb_count = 0
        self._last_odom_rx_time = 0.0

        qos = QoSProfile(
            reliability=ReliabilityPolicy.BEST_EFFORT,
            history=HistoryPolicy.KEEP_LAST,
            depth=1,
        )
        self.cb_group = ReentrantCallbackGroup()

        self.odom_topics = [
            "/my_go2/robot_odom",
            "/utlidar/robot_odom",
            "/uslam/localization/odom",
        ]

        self._subs.append(
            self.create_subscription(
                JointState,
                "/joint_states",
                self.joint_callback,
                qos,
                callback_group=self.cb_group,
            )
        )
        for odom_topic in self.odom_topics:
            self._subs.append(
                self.create_subscription(
                    Odometry,
                    odom_topic,
                    self.odom_callback,
                    qos,
                    callback_group=self.cb_group,
                )
            )
            print(f"Odom 토픽 구독 중: {odom_topic}")

        self.joint_names = [
            "FR_hip_joint",
            "FR_thigh_joint",
            "FR_calf_joint",
            "FL_hip_joint",
            "FL_thigh_joint",
            "FL_calf_joint",
            "RR_hip_joint",
            "RR_thigh_joint",
            "RR_calf_joint",
            "RL_hip_joint",
            "RL_thigh_joint",
            "RL_calf_joint",
        ]

        self.create_timer(2.0, self.log_odom_subscription_diagnostics)

    def _format_pub_qos(self, info) -> str:
        try:
            qos = info.qos_profile
            rel = str(qos.reliability).split(".")[-1]
            dur = str(qos.durability).split(".")[-1]
            hist = str(qos.history).split(".")[-1]
            return f"reliability={rel}, durability={dur}, history={hist}, depth={qos.depth}"
        except Exception:
            return "qos=(unknown)"

    def _diag_topic(self, topic: str, expected_type: str, last_rx: float, cb_count: int) -> None:
        infos = self.get_publishers_info_by_topic(topic)
        now = time.time()
        age = (now - last_rx) if last_rx > 0.0 else None
        age_str = f"{age:.2f}s ago" if age is not None else "never"
        print(
            f"[ODOM_DIAG] topic={topic} expected={expected_type} pubs={len(infos)} "
            f"callbacks={cb_count} last_rx={age_str}"
        )
        for i, info in enumerate(infos):
            print(
                f"[ODOM_DIAG] pub[{i}] node={info.node_name} ns={info.node_namespace} "
                f"type={info.topic_type} {self._format_pub_qos(info)}"
            )

    def log_odom_subscription_diagnostics(self) -> None:
        try:
            for topic in self.odom_topics:
                self._diag_topic(
                    topic,
                    "nav_msgs/msg/Odometry",
                    self._last_odom_rx_time,
                    self._odom_cb_count,
                )
        except Exception as e:
            print(f"[ODOM_DIAG] 진단 실패: {e}")

    def joint_callback(self, msg: JointState) -> None:
        if not msg.name or not msg.position:
            return

        name_to_idx = {name: i for i, name in enumerate(self.joint_names)}
        for name, pos in zip(msg.name, msg.position):
            idx = name_to_idx.get(name)
            if idx is not None:
                self.joint_positions[idx] = float(pos)

    def odom_callback(self, msg: Odometry) -> None:
        self._odom_cb_count += 1
        self._last_odom_rx_time = time.time()
        p = msg.pose.pose.position
        q = msg.pose.pose.orientation
        self.base_pos[:] = (p.x, p.y, p.z)
        self.base_ori[:] = (q.w, q.x, q.y, q.z)

        now = time.time()
        if not hasattr(self, "_last_odom_diag_time"):
            self._last_odom_diag_time = 0.0
        if now - self._last_odom_diag_time >= 1.0:
            self._last_odom_diag_time = now
            print(
                f"[ODOM] pos=({p.x:.3f},{p.y:.3f},{p.z:.3f}) "
                f"ori_wxyz=({q.w:.3f},{q.x:.3f},{q.y:.3f},{q.z:.3f})"
            )

    def update_robot(self) -> None:
        self.articulation.set_joint_positions(
            self.joint_positions, joint_indices=self.get_joint_indices()
        )
        self.articulation.set_world_pose(self.base_pos, self.base_ori)

        now = time.time()
        if not hasattr(self, "_last_pose_diag_time"):
            self._last_pose_diag_time = 0.0
        if now - self._last_pose_diag_time >= 1.0:
            self._last_pose_diag_time = now
            try:
                pos, _ = self.articulation.get_world_pose()
                if hasattr(pos, "tolist"):
                    pos = pos.tolist()
                print(
                    f"[POSE] target=({self.base_pos[0]:.3f},{self.base_pos[1]:.3f},{self.base_pos[2]:.3f}) "
                    f"actual=({pos[0]:.3f},{pos[1]:.3f},{pos[2]:.3f})"
                )
            except Exception as e:
                print(f"[POSE] 진단 실패: {e}")

    def get_joint_indices(self) -> list:
        if not hasattr(self, "_joint_indices"):
            all_joints = self.articulation.dof_names
            self._joint_indices = [
                all_joints.index(name) for name in self.joint_names if name in all_joints
            ]
        return self._joint_indices


def main() -> None:
    world = World(stage_units_in_meters=1.0)
    world.scene.add_default_ground_plane()

    urdf_path = "/home/jnu/go2_ws/src/go2_description/urdf/go2_description.urdf"
    enable_extension("isaacsim.asset.importer.urdf")
    from isaacsim.asset.importer.urdf import _urdf

    import_config = _urdf.ImportConfig()
    import_config.merge_fixed_joints = False
    import_config.fix_base = False
    import_config.make_default_prim = True
    import_config.self_collision = False
    import_config.create_physics_scene = True
    import_config.import_inertia_tensor = False
    import_config.distance_scale = 1.0
    import_config.density = 0.0

    dest_path, prim_path = "/tmp/go2.usd", "/World/Go2"
    omni.kit.commands.execute(
        "URDFParseAndImportFile",
        urdf_path=urdf_path,
        import_config=import_config,
        dest_path=dest_path,
    )
    add_reference_to_stage(usd_path=dest_path, prim_path=prim_path)

    go2_robot = Articulation(prim_path=prim_path, name="go2")
    world.scene.add(go2_robot)

    rclpy.init()
    visualizer = Go2Visualizer(go2_robot)

    executor = rclpy.executors.MultiThreadedExecutor()
    executor.add_node(visualizer)
    spin_thread = threading.Thread(target=executor.spin, daemon=True)
    spin_thread.start()

    world.reset()

    try:
        while simulation_app.is_running():
            world.step(render=True)
            if go2_robot.handles_initialized:
                visualizer.update_robot()
    except Exception as e:
        print(f"루프 실행 중 오류: {e}")
    finally:
        executor.shutdown()
        visualizer.destroy_node()
        rclpy.shutdown()
        simulation_app.close()


if __name__ == "__main__":
    main()
