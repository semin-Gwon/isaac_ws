
# 1. 표준 라이브러리 및 서드파티 필수 패키지
import argparse
import sys
import os
import time
import threading
import math
import numpy as np
import cv2
import torch
import gymnasium as gym

# 2. Isaac Lab 실행기 및 필수 유틸리티
try:
    from isaaclab.app import AppLauncher
except ImportError:
    print("오류: 'isaaclab' 패키지를 찾을 수 없습니다.")
    sys.exit(1)

# AppLauncher용 인자 파서 설정
parser = argparse.ArgumentParser(description="Go2 SLAM RoboStack (World Class Version)")
parser.add_argument("--task", type=str, default="Isaac-Velocity-Flat-Unitree-Go2-Play-v0", help="Task name.")
parser.add_argument("--seed", type=int, default=None, help="Random seed.")
parser.add_argument("--num_envs", type=int, default=1, help="Number of environments.")
parser.add_argument("--use_pretrained_checkpoint", action="store_true", default=True, help="Use checkpoint.")

AppLauncher.add_app_launcher_args(parser)
args_cli = parser.parse_args()

# Isaac Sim 앱 실행
app_launcher = AppLauncher(args_cli)
simulation_app = app_launcher.app

# [NEW] 필요한 익스텐션 즉시 활성화 (sys.argv 충돌 방지)
import omni.kit.app
ext_manager = omni.kit.app.get_app().get_extension_manager()
ext_manager.set_extension_enabled_immediate("omni.isaac.core", True)
ext_manager.set_extension_enabled_immediate("omni.isaac.sensor", True)
ext_manager.set_extension_enabled_immediate("isaacsim.ros2.bridge", True)

# ============================================================================
# 앱 실행 후 모듈 임포트 (Isaac Sim 관련 모듈은 여기서부터 안전하게 사용할 수 있음)
# ============================================================================
import carb
import omni
import omni.kit.commands
import omni.ui as ui
import omni.graph.core as og
from omni.kit.viewport.utility import create_viewport_window
from omni.isaac.core import World
from omni.isaac.core.utils.stage import add_reference_to_stage
from omni.isaac.core.utils.prims import get_prim_at_path
from omni.isaac.core.utils.extensions import enable_extension
from omni.isaac.core.articulations import Articulation
from omni.isaac.core.objects import (
    VisualCuboid,
    VisualSphere,
    VisualCone,
    VisualCylinder,
    VisualCapsule,
)
from omni.isaac.sensor import Camera

# 4. USD/PXR 관련 임포트
from pxr import Usd, UsdGeom, Gf, Sdf, UsdShade, UsdPhysics
from omni.isaac.core.utils.stage import add_reference_to_stage # [NEW] 직접 로딩용

# 5. Isaac Sim Extensions & ROS 2 Bridge
from isaacsim.core.utils import extensions
# from isaacsim.ros2.bridge import ROS2CameraHelper, ROS2CameraInfoHelper # (참고용, 실제 사용은 OmniGraph 노드로)

# [FIX] 확장 로드 보장
enable_extension("isaacsim.ros2.bridge")
simulation_app.update() # 중요: 확장이 로드되려면 최소 1회 업데이트 필요

enable_extension("omni.isaac.sensor")

# [FIX] 외부 RMW 설정 제거 (Isaac Sim 내부 기본값 사용 유도)
os.environ.pop("RMW_IMPLEMENTATION", None)

# 6. ROS 2 (RoboStack 환경)
import rclpy
from rclpy.node import Node
from rclpy.qos import QoSProfile, ReliabilityPolicy, HistoryPolicy
from rclpy.callback_groups import ReentrantCallbackGroup
from sensor_msgs.msg import Image, CameraInfo
from nav_msgs.msg import Odometry
from geometry_msgs.msg import TransformStamped, Quaternion, Pose, Twist
from tf2_msgs.msg import TFMessage
from std_msgs.msg import Header

# 7. Isaac Lab & RL 관련 임포트
from isaaclab_rl.rsl_rl import RslRlVecEnvWrapper
from isaaclab.devices import Se2Keyboard, Se2KeyboardCfg
from isaaclab.utils.pretrained_checkpoint import get_published_pretrained_checkpoint
from isaaclab.utils import configclass
import isaaclab_tasks  # noqa
from isaaclab_tasks.utils.hydra import hydra_task_config # Hydra 활성화
from isaaclab_tasks.manager_based.locomotion.velocity.config.go2.flat_env_cfg import UnitreeGo2FlatEnvCfg_PLAY
from isaaclab_tasks.manager_based.locomotion.velocity.config.go2.agents.rsl_rl_ppo_cfg import UnitreeGo2RoughPPORunnerCfg
from rsl_rl.runners import OnPolicyRunner
from my_slam_env import MySlamEnvCfg  # [NEW] 커스텀 환경 설정 임포트
# 8. Robot Specific Messages (Optional)
try:
    from unitree_go.msg import LowState
    HAS_LOWSTATE = True
except ImportError:
    print("unitree_go.msg.LowState를 임포트할 수 없습니다. 관절 상태 수신이 비활성화됩니다.")
    HAS_LOWSTATE = False
    class LowState:
        pass


# [NEW] WasdKeyboard 클래스 정의 (go2_sim.py 참조)
class WasdKeyboard(Se2Keyboard):
    def _create_key_bindings(self):
        self._INPUT_KEY_MAPPING = {
            "W": np.asarray([1.0, 0.0, 0.0]) * self.v_x_sensitivity,
            "S": np.asarray([-1.0, 0.0, 0.0]) * self.v_x_sensitivity,
            "A": np.asarray([0.0, 1.0, 0.0]) * self.v_y_sensitivity,
            "D": np.asarray([0.0, -1.0, 0.0]) * self.v_y_sensitivity,
            "Q": np.asarray([0.0, 0.0, 1.0]) * self.omega_z_sensitivity,
            "E": np.asarray([0.0, 0.0, -1.0]) * self.omega_z_sensitivity,
            "K": np.asarray([0.0, 0.0, 0.0]),
        }


class Go2SlamPublisher(Node):
    """Go2 로봇의 센서 데이터를 ROS 2로 발행하는 노드 (SLAM용)"""

    def __init__(self):
        super().__init__("go2_slam_publisher")

        # QoS 설정 (센서 데이터용)
        # [FIX] RELIABLE로 변경하여 외부 ROS 2 노드와 호환성 확보
        sensor_qos = QoSProfile(
            reliability=ReliabilityPolicy.RELIABLE,
            history=HistoryPolicy.KEEP_LAST,
            depth=10,  # RELIABLE 사용 시 depth를 늘려 안정성 향상
        )

        # Publisher 생성
        self.image_pub = self.create_publisher(Image, "/camera/image_raw", sensor_qos)
        self.camera_info_pub = self.create_publisher(
            CameraInfo, "/camera/camera_info", sensor_qos
        )
        # [NEW] Depth Publisher 추가 (RTAB-Map용 필수)
        self.depth_pub = self.create_publisher(Image, "/camera/depth/image_raw", sensor_qos)
        
        self.odom_pub = self.create_publisher(Odometry, "/odom", sensor_qos)
        self.tf_pub = self.create_publisher(TFMessage, "/tf", sensor_qos)

        self.camera_sensor = None
        self.robot_prim = None
        self.screen_prim = None  # 가상 스크린 참조
        self.texture_provider = None  # 동적 텍스처 제공자

        self.get_logger().info("Go2 SLAM Publisher 초기화 완료")

        # [NEW] slam_cam.py 방식: 로봇 상태 수신 및 제어 변수
        self.articulation = None
        self.joint_positions = np.zeros(12)
        # 초기 위치를 높게 설정하여 바닥에 묻히지 않도록 함
        self.base_pos = np.array([0.0, 0.0, 0.5])
        self.base_ori = np.array([1.0, 0.0, 0.0, 0.0])

        self.cb_group = ReentrantCallbackGroup()

        # [FIX] 구독(Subscription) 로직 제거 - 시뮬레이션이 Ground Truth임
        # 시뮬레이션이 ROS 상태를 따르는 것이 아니라, 시뮬레이션 상태를 ROS로 보냄.
        # 따라서 Joint/Odom 구독자 제거하여 로봇 제어 간섭(후진 문제) 해결.

    def update_robot(self) -> None:
        """가상 스크린만 업데이트합니다 (로봇 제어 권한 제거)."""
        # [FIX] Articulation 수동 조작 코드 제거 (RL이 제어)
        self.update_virtual_screen()

    def update_virtual_screen(self) -> None:
        """가상 스크린을 로봇의 현재 위치에 따라 업데이트합니다."""
        if self.screen_prim is None:
            return

        try:
            # 로봇의 현재 위치와 방향
            pos = self.base_pos
            ori = self.base_ori  # [w, x, y, z] quaternion

            # Quaternion을 회전 행렬로 변환
            w, x, y, z = ori[0], ori[1], ori[2], ori[3]

            # Yaw 각도 추출 (Z축 회전)
            yaw = math.atan2(2.0 * (w * z + x * y), 1.0 - 2.0 * (y * y + z * z))

            # 로봇 전방 방향 벡터 계산
            forward_x = math.cos(yaw)
            forward_y = math.sin(yaw)

            # 스크린 위치: 로봇 전방 0.5m, 위로 0.2m
            screen_offset_forward = 0.5
            screen_offset_up = 0.2

            screen_x = pos[0] + forward_x * screen_offset_forward
            screen_y = pos[1] + forward_y * screen_offset_forward
            screen_z = pos[2] + screen_offset_up

            # 스크린 위치 업데이트
            xform = UsdGeom.Xformable(self.screen_prim)
            
            # [FIX] XformOp 안전 처리
            ops = xform.GetOrderedXformOps()
            if not ops:
                # Op가 없으면 새로 추가
                translate_op = xform.AddTranslateOp()
                rotate_op = xform.AddRotateZOp()
            else:
                # 기존 Op 사용 (순서 가정: Translate, Rotate. 만약 하나만 있으면 추가)
                translate_op = ops[0]
                if len(ops) > 1:
                    rotate_op = ops[1]
                else:
                    rotate_op = xform.AddRotateZOp()

            # [DEBUG] 좌표 업데이트 정보 출력 (100번 호출마다 한 번씩만)
            # if np.random.rand() < 0.01:
            #     print(f"[DEBUG] Virtual Screen Update: Pos=({screen_x:.2f}, {screen_y:.2f}, {screen_z:.2f}), Yaw={math.degrees(yaw):.2f}")

            translate_op.Set(Gf.Vec3d(screen_x, screen_y, screen_z))
            # 스크린이 로봇을 향하도록 180도 회전 + 로봇의 yaw
            rotate_op.Set(math.degrees(yaw + math.pi))

        except Exception as e:
            print(f"[WARN] 가상 스크린 업데이트 오류: {e}")

    def get_joint_indices(self) -> list:
        """Articulation 내의 관절 인덱스를 매핑합니다."""
        if not hasattr(self, "_joint_indices"):
            if self.articulation:
                all_joints = self.articulation.dof_names
                if not hasattr(self, "joint_names"): # 안전장치
                    return []
                indices = [
                    all_joints.index(name)
                    for name in self.joint_names
                    if name in all_joints
                ]
                self._joint_indices = indices
            else:
                return []
        return self._joint_indices

    def get_camera_info(self, stamp, width, height):
        """카메라 내부 파라미터를 담은 CameraInfo 메시지 생성"""
        camera_info_msg = CameraInfo()
        camera_info_msg.header.stamp = stamp
        camera_info_msg.header.frame_id = "camera_link"
        camera_info_msg.width = width
        camera_info_msg.height = height
        
        # [FIX] RTAB-Map을 위한 Distortion Model 설정
        camera_info_msg.distortion_model = "plumb_bob"
        camera_info_msg.d = [0.0, 0.0, 0.0, 0.0, 0.0]  # 시뮬레이션 카메라는 왜곡 없음

        try:
            # Isaac Sim Camera Sensor로부터 Intrinsics 가져오기
            if self.camera_sensor:
                # 1. Intrinsic Matrix (K) 가져오기
                # get_intrinsics_matrix() -> (3, 3) numpy array
                if hasattr(self.camera_sensor, "get_intrinsics_matrix"):
                    k_matrix = self.camera_sensor.get_intrinsics_matrix()
                    if hasattr(k_matrix, "cpu"): k_matrix = k_matrix.cpu().numpy() # Tensor일 경우
                    if k_matrix.ndim == 3: k_matrix = k_matrix[0] # (1, 3, 3)일 경우

                    # K 행렬 (3x3 -> 9)
                    camera_info_msg.k = k_matrix.flatten().tolist()
                    
                    # R 행렬 (Identity 3x3 -> 9)
                    camera_info_msg.r = [1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0]

                    # P 행렬 (Projection 3x4 -> 12)
                    # [fx, 0, cx, 0]
                    # [0, fy, cy, 0]
                    # [0, 0, 1, 0]
                    p_matrix = np.zeros((3, 4))
                    p_matrix[:3, :3] = k_matrix
                    camera_info_msg.p = p_matrix.flatten().tolist()
                    
                elif hasattr(self.camera_sensor, "data") and hasattr(self.camera_sensor.data, "intrinsic_matrices"):
                     # IsaacLab Camera Data
                     k_matrix = self.camera_sensor.data.intrinsic_matrices[0] # 첫 번째 환경
                     if hasattr(k_matrix, "cpu"): k_matrix = k_matrix.cpu().numpy()
                     
                     camera_info_msg.k = k_matrix.flatten().tolist()
                     camera_info_msg.r = [1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0]
                     p_matrix = np.zeros((3, 4))
                     p_matrix[:3, :3] = k_matrix
                     camera_info_msg.p = p_matrix.flatten().tolist()
            
        except Exception as e:
            self.get_logger().warn(f"Camera Info 생성 중 오류: {e}")

        return camera_info_msg

    def publish_data(self):
        """센서 데이터 조회 및 ROS 2 발행"""
        current_time = self.get_clock().now().to_msg()

        # 1. 카메라 데이터 발행
        if self.camera_sensor:
            try:
                # Replicator Wrapper 호환
                if hasattr(self.camera_sensor, "get_current_frame"):
                    frame = self.camera_sensor.get_current_frame()
                    if isinstance(frame, dict):
                        if "rgb" in frame:
                            rgba = frame["rgb"]
                        elif "rgba" in frame:
                            rgba = frame["rgba"]
                        elif "data" in frame:
                            rgba = frame["data"]
                        else:
                            rgba = None
                    else:
                        rgba = frame
                else:
                    rgba = self.camera_sensor.get_rgba()

                if rgba is not None:
                    # 데이터 유효성 검사
                    if not hasattr(rgba, "shape") or rgba.ndim != 3 or rgba.size == 0:
                        return

                    # 디버그: 데이터 통계 출력 (처음 10회만)
                    if not hasattr(self, "_data_debug_count"):
                        self._data_debug_count = 0
                    if self._data_debug_count < 10:
                        self.get_logger().info(f"자료형: {rgba.dtype}, Shape: {rgba.shape}, Max: {np.max(rgba)}, Min: {np.min(rgba)}, Mean: {np.mean(rgba)}")
                        self._data_debug_count += 1

                    # [NEW] 데이터가 모두 0인지 확인 (Black frame 체크)
                    if np.max(rgba) == 0:
                        if not hasattr(self, "_black_frame_warned") or not self._black_frame_warned:
                            self.get_logger().warn("카메라 데이터가 모두 0(Black)입니다. GPU 버퍼 대기 중...")
                            self._black_frame_warned = True
                        return
                    else:
                        if hasattr(self, "_black_frame_warned") and self._black_frame_warned:
                            self.get_logger().info("카메라 데이터 수신 시작 (Non-zero)")
                            self._black_frame_warned = False

                    # 데이터 차원 확인 및 RGB 변환 + uint8 강제 변환
                    if rgba.shape[2] == 4:
                        rgb = rgba[:, :, :3]
                    elif rgba.shape[2] == 3:
                        rgb = rgba
                    else:
                        return
                    
                    # [FIX] 자료형이 float일 경우 255를 곱해야 할 수도 있음 (0~1 범위일 경우)
                    if rgba.dtype == np.float32 or rgba.dtype == np.float64:
                        if np.max(rgba) <= 1.0:
                            rgb = (rgb * 255).astype(np.uint8)
                        else:
                            rgb = rgb.astype(np.uint8)
                    else:
                        rgb = rgb.astype(np.uint8)

                    # [FIX] 메모리 연속성 보장 및 바이트 변환
                    rgb_contiguous = np.ascontiguousarray(rgb)
                    
                    msg = Image()
                    msg.header.stamp = current_time
                    msg.header.frame_id = "camera_link"
                    msg.height = rgb.shape[0]
                    msg.width = rgb.shape[1]
                    msg.encoding = "rgb8"
                    msg.is_bigendian = False
                    msg.step = 3 * rgb.shape[1]
                    msg.data = rgb_contiguous.tobytes()
                    
                    self.image_pub.publish(msg)

                # [FIX] Camera Info 발행 (Intrinsics 포함)
                info_msg = self.get_camera_info(current_time, msg.width, msg.height)
                self.camera_info_pub.publish(info_msg)
            
            except Exception as e:
                self.get_logger().error(f"Error publishing camera data: {e}")

            # 1-2. Depth 데이터 발행 (RTAB-Map 3D 매핑용)
            try:
                # 'distance_to_image_plane' 데이터 가져오기 (미터 단위)
                depth_data = None
                if hasattr(self.camera_sensor, "get_current_frame"):
                    frame = self.camera_sensor.get_current_frame()
                    if isinstance(frame, dict) and "distance_to_image_plane" in frame:
                        depth_data = frame["distance_to_image_plane"]
                
                # 데이터가 없으면 get_depth() 시도 (설정에 따라 다름)
                if depth_data is None and hasattr(self.camera_sensor, "get_depth"):
                    depth_data = self.camera_sensor.get_depth()

                if depth_data is not None:
                    # 유효성 검사 및 전처리
                    if hasattr(depth_data, "cpu"): depth_data = depth_data.cpu().numpy()
                    if isinstance(depth_data, np.ndarray):
                        # 차원 정리 (H, W, 1) -> (H, W) or (1, H, W) -> (H, W)
                        if depth_data.ndim == 3:
                            depth_data = depth_data.squeeze()
                        
                        # NaN/Inf 처리 (0.0으로 치환)
                        depth_data = np.nan_to_num(depth_data, nan=0.0, posinf=0.0, neginf=0.0)
                        
                        # float32로 변환 (ROS Image encoding: 32FC1)
                        depth_float = depth_data.astype(np.float32)
                        
                        # 메시지 생성
                        depth_msg = Image()
                        depth_msg.header.stamp = current_time
                        depth_msg.header.frame_id = "camera_link"
                        depth_msg.height = depth_float.shape[0]
                        depth_msg.width = depth_float.shape[1]
                        depth_msg.encoding = "32FC1"
                        depth_msg.is_bigendian = False
                        depth_msg.step = 4 * depth_float.shape[1] # 4 bytes * width
                        depth_msg.data = depth_float.tobytes()
                        
                        self.depth_pub.publish(depth_msg)

                        # [DEBUG] Depth 데이터 통계 출력 (100번 호출마다 한 번씩만)
                        if not hasattr(self, "_depth_debug_count"):
                            self._depth_debug_count = 0
                        self._depth_debug_count += 1
                        if self._depth_debug_count % 100 == 0:
                            min_val = np.min(depth_float)
                            max_val = np.max(depth_float)
                            mean_val = np.mean(depth_float)
                            self.get_logger().info(f"[DEBUG] Depth Updated: Min={min_val:.3f}, Max={max_val:.3f}, Mean={mean_val:.3f}")

            except Exception as e:
                self.get_logger().error(f"Error publishing depth data: {e}")

        # 2. 오도메트리 및 TF 발행
        if self.articulation:
            try:
                # Articulation에서 현재 위치 및 방향 가져오기
                if hasattr(self.articulation, "data") and hasattr(self.articulation.data, "root_pos_w"):
                     # IsaacLab
                     position = self.articulation.data.root_pos_w
                     orientation = self.articulation.data.root_quat_w
                     
                     # 텐서인 경우 CPU/Numpy 변환
                     if hasattr(position, "cpu"): position = position.cpu().numpy()
                     if hasattr(orientation, "cpu"): orientation = orientation.cpu().numpy()
                     
                     # 차원 축소 (1, 3) -> (3,)
                     if position.ndim == 2: position = position[0]
                     if orientation.ndim == 2: orientation = orientation[0]
                     
                else:
                     # Isaac Core
                     position, orientation = self.articulation.get_world_pose()
                
                # Odom 메시지 생성
                odom_msg = Odometry()
                odom_msg.header.stamp = current_time
                odom_msg.header.frame_id = "odom"
                odom_msg.child_frame_id = "base_link"
                
                # 위치 설정
                odom_msg.pose.pose.position.x = float(position[0])
                odom_msg.pose.pose.position.y = float(position[1])
                odom_msg.pose.pose.position.z = float(position[2])
                
                # 방향 설정 (quaternion: [w, x, y, z] -> ROS는 [x, y, z, w])
                odom_msg.pose.pose.orientation.x = float(orientation[1])
                odom_msg.pose.pose.orientation.y = float(orientation[2])
                odom_msg.pose.pose.orientation.z = float(orientation[3])
                odom_msg.pose.pose.orientation.w = float(orientation[0])
                
                # 속도는 0으로 설정 (정지 상태)
                odom_msg.twist.twist.linear.x = 0.0
                odom_msg.twist.twist.linear.y = 0.0
                odom_msg.twist.twist.linear.z = 0.0
                odom_msg.twist.twist.angular.x = 0.0
                odom_msg.twist.twist.angular.y = 0.0
                odom_msg.twist.twist.angular.z = 0.0
                
                # Odom 발행
                self.odom_pub.publish(odom_msg)
                
                # TF 발행 (odom -> base_link)
                tf_msg = TFMessage()
                transform = TransformStamped()
                transform.header.stamp = current_time
                transform.header.frame_id = "odom"
                transform.child_frame_id = "base_link"
                transform.transform.translation.x = float(position[0])
                transform.transform.translation.y = float(position[1])
                transform.transform.translation.z = float(position[2])
                transform.transform.rotation.x = float(orientation[1])
                transform.transform.rotation.y = float(orientation[2])
                transform.transform.rotation.z = float(orientation[3])
                transform.transform.rotation.w = float(orientation[0])
                
                tf_msg.transforms.append(transform)
                
                # TF 발행 (camera_link도 추가)
                camera_tf = TransformStamped()
                camera_tf.header.stamp = current_time
                camera_tf.header.frame_id = "base_link"
                camera_tf.child_frame_id = "camera_link"
                camera_tf.transform.translation.x = 0.3  # 카메라 오프셋
                camera_tf.transform.translation.y = 0.0
                camera_tf.transform.translation.z = 0.2
                camera_tf.transform.rotation.x = 0.0
                camera_tf.transform.rotation.y = 0.0
                camera_tf.transform.rotation.z = 0.0
                camera_tf.transform.rotation.w = 1.0
                
                tf_msg.transforms.append(camera_tf)
                self.tf_pub.publish(tf_msg)
                
            except Exception as e:
                self.get_logger().error(f"Error publishing odom/tf: {e}")


def setup_ui_window(camera_sensor):
    """칵셔라 뷰를 보여주는 UI 창 생성"""

    ui_window = ui.Window("Robot Camera View", width=450, height=680)

    # 이미지 프로바이더
    ui_image_provider = ui.ByteImageProvider()
    ui_depth_provider = ui.ByteImageProvider()

    with ui_window.frame:
        with ui.VStack():
            # RGB 칵셔라 피드
            ui.Label("RGB Camera Feed", height=20)
            try:
                ui.ImageWithProvider(ui_image_provider, width=400, height=300)
            except AttributeError:
                print("[WARN] ImageWithProvider 찾을 수 없음")

            ui.Spacer(height=10)

            # Depth 칵셔라 피드
            ui.Label("Depth Camera Feed", height=20)
            try:
                ui.ImageWithProvider(ui_depth_provider, width=400, height=300)
            except AttributeError:
                print("[WARN] ImageWithProvider 찾을 수 없음 (Depth)")

    # UI 업데이트 함수
    def update_ui(texture_provider=None):
        try:
            # RGBA 이미지
            if hasattr(camera_sensor, "get_rgba"):
                rgba = camera_sensor.get_rgba()
                if rgba is not None and hasattr(rgba, "shape") and rgba.size > 0:
                    if rgba.ndim == 3:
                        if rgba.shape[2] == 3:
                            rgba = cv2.cvtColor(rgba, cv2.COLOR_RGB2RGBA)

                        h, w = rgba.shape[:2]
                        if rgba.dtype != np.uint8:
                            rgba = (rgba).astype(np.uint8)

                        ui_image_provider.set_bytes_data(
                            rgba.flatten().tolist(), [w, h]
                        )

                        # 가상 스크린 텍스처 업데이트
                        if texture_provider:
                            texture_provider.set_bytes_data(
                                rgba.flatten().tolist(), [w, h]
                            )

            # Depth 이미지
            if hasattr(camera_sensor, "get_depth"):
                depth = camera_sensor.get_depth()
                if depth is not None and hasattr(depth, "shape") and depth.size > 0:
                    # NaN 및 inf 값 처리
                    depth_clean = np.nan_to_num(depth, nan=0.0, posinf=0.0, neginf=0.0)
                    # 유효한 depth 범위로 클리핑 (0.1m ~ 10m)
                    depth_clean = np.clip(depth_clean, 0.1, 10.0)
                    # 정규화
                    depth_min, depth_max = depth_clean.min(), depth_clean.max()
                    if depth_max > depth_min:
                        depth_normalized = (
                            (depth_clean - depth_min) / (depth_max - depth_min) * 255
                        ).astype(np.uint8)
                    else:
                        depth_normalized = np.zeros_like(depth_clean, dtype=np.uint8)
                    depth_colored = cv2.applyColorMap(
                        depth_normalized, cv2.COLORMAP_TURBO
                    )
                    depth_rgba = cv2.cvtColor(depth_colored, cv2.COLOR_BGR2RGBA)

                    h, w = depth_rgba.shape[:2]
                    ui_depth_provider.set_bytes_data(
                        depth_rgba.flatten().tolist(), [w, h]
                    )
        except Exception as e:
            pass

    return ui_window, update_ui, ui_image_provider  # ui_image_provider 반환 추가


def setup_virtual_screen(stage, screen_path="/World/Go2/VisualScreen"):
    """영상 시각화를 위한 가상 스크린 평면과 자체 발광 재질을 생성합니다."""
    print(f"가상 스크린 생성 중: {screen_path}")

    # 평면 메쉬 생성 (YZ 평면 상에 생성하여 전방X를 향하도록 설정)
    plane = UsdGeom.Mesh.Define(stage, screen_path)
    h_w, h_h = 0.2, 0.15
    # 로봇을 마주볻록 정점 정의
    points = [
        Gf.Vec3f(0, -h_w, -h_h),
        Gf.Vec3f(0, h_w, -h_h),
        Gf.Vec3f(0, h_w, h_h),
        Gf.Vec3f(0, -h_w, h_h),
    ]
    plane.CreatePointsAttr(points)
    plane.CreateFaceVertexCountsAttr([4])
    plane.CreateFaceVertexIndicesAttr([0, 1, 2, 3])

    # 텍스처 좌표 (UV) 설정
    primvars_api = UsdGeom.PrimvarsAPI(plane)
    st_primvar = primvars_api.CreatePrimvar(
        "st", Sdf.ValueTypeNames.TexCoord2fArray, UsdGeom.Tokens.varying
    )
    st_primvar.Set([(0, 1), (1, 1), (1, 0), (0, 0)])

    # 위치 설정은 update_virtual_screen()에서 동적으로 처리됨
    xform = UsdGeom.Xformable(plane)
    xform.AddTranslateOp().Set(Gf.Vec3d(0.5, 0.0, 0.2))  # 초기 위치

    # 자체 발광 재질 설정 (Emissive Material)
    mat_path = "/World/Looks/ScreenMat"
    material = UsdShade.Material.Define(stage, mat_path)
    shader = UsdShade.Shader.Define(stage, f"{mat_path}/PBRShader")
    shader.CreateIdAttr("UsdPreviewSurface")
    material.CreateSurfaceOutput().ConnectToSource(shader.ConnectableAPI(), "surface")

    # 동적 텍스처 연결 (dynamic://ros_screen)
    tex = UsdShade.Shader.Define(stage, f"{mat_path}/DiffuseTexture")
    tex.CreateIdAttr("UsdUVTexture")
    tex.CreateInput("file", Sdf.ValueTypeNames.Asset).Set("dynamic://ros_screen")

    uv_reader = UsdShade.Shader.Define(stage, f"{mat_path}/UVReader")
    uv_reader.CreateIdAttr("UsdPrimvarReader_float2")
    uv_reader.CreateInput("varname", Sdf.ValueTypeNames.Token).Set("st")
    tex.CreateInput("st", Sdf.ValueTypeNames.Float2).ConnectToSource(
        uv_reader.ConnectableAPI(), "result"
    )

    shader.CreateInput("diffuseColor", Sdf.ValueTypeNames.Color3f).ConnectToSource(
        tex.ConnectableAPI(), "rgb"
    )
    shader.CreateInput("emissiveColor", Sdf.ValueTypeNames.Color3f).ConnectToSource(
        tex.ConnectableAPI(), "rgb"
    )
    shader.CreateInput("useSpecularWorkflow", Sdf.ValueTypeNames.Int).Set(0)

    UsdShade.MaterialBindingAPI(plane).Bind(material)

    # plane prim 반환
    return plane


def setup_ros2_camera_graph(camera_prim_path: str):
    """숨겨진 뷰포트에서 렌더 프로덕트 생성 → OmniGraph ROS2 퍼블리시 (RTAB-Map용)."""
    # 숨겨진 뷰포트 생성 (메인 뷰포트에 영향 없음)
    vp_window = create_viewport_window(
        "ROS2_Camera", width=640, height=480, visible=False
    )
    vp_api = vp_window.viewport_api
    vp_api.set_active_camera(camera_prim_path)
    
    rp_path = vp_api.get_render_product_path()
    print(f"[INFO] ROS2 Camera Grpah 생성 중. Render Product: {rp_path}")

    keys = og.Controller.Keys
    (ros_camera_graph, _, _, _) = og.Controller.edit(
        {
            "graph_path": "/ROS2_Camera",
            "evaluator_name": "push",
            "pipeline_stage": og.GraphPipelineStage.GRAPH_PIPELINE_STAGE_ONDEMAND,
        },
        {
            keys.CREATE_NODES: [
                ("OnTick", "omni.graph.action.OnTick"),
                ("cameraHelperRgb", "isaacsim.ros2.bridge.ROS2CameraHelper"),
                ("cameraHelperDepth", "isaacsim.ros2.bridge.ROS2CameraHelper"),
                ("cameraHelperInfo", "isaacsim.ros2.bridge.ROS2CameraInfoHelper"),
            ],
            keys.CONNECT: [
                ("OnTick.outputs:tick", "cameraHelperRgb.inputs:execIn"),
                ("OnTick.outputs:tick", "cameraHelperDepth.inputs:execIn"),
                ("OnTick.outputs:tick", "cameraHelperInfo.inputs:execIn"),
            ],
            keys.SET_VALUES: [
                ("cameraHelperRgb.inputs:renderProductPath", rp_path),
                ("cameraHelperRgb.inputs:frameId", "camera_link"),
                ("cameraHelperRgb.inputs:topicName", "camera/color/image_raw"),
                ("cameraHelperRgb.inputs:type", "rgb"),
                ("cameraHelperDepth.inputs:renderProductPath", rp_path),
                ("cameraHelperDepth.inputs:frameId", "camera_link"),
                ("cameraHelperDepth.inputs:topicName", "camera/depth/image_rect_raw"),
                ("cameraHelperDepth.inputs:type", "depth"),
                ("cameraHelperInfo.inputs:renderProductPath", rp_path),
                ("cameraHelperInfo.inputs:frameId", "camera_link"),
                ("cameraHelperInfo.inputs:topicName", "camera/camera_info"),
            ],
        },
    )
    og.Controller.evaluate_sync(ros_camera_graph)
    print("[INFO] ROS2 카메라 퍼블리셔 OmniGraph 설정 완료")

# [NEW] go2_sim.py의 MySlamEnvCfg 로직 복제 (SLAM 및 키보드 제어 최적화)
@configclass
class MySlamEnvCfg(UnitreeGo2FlatEnvCfg_PLAY):
    def __post_init__(self):
        super().__post_init__()
        
        # 제어 설정: Heading Command 비활성화 (Velocity Control 모드)
        if hasattr(self.commands, "base_velocity"):
            # 랜덤 명령 생성 방지 (키보드 입력 유지)
            self.commands.base_velocity.resampling_time_range = (1.0e9, 1.0e9)
            self.commands.base_velocity.debug_vis = False
            self.commands.base_velocity.heading_command = False

@hydra_task_config(args_cli.task, "rsl_rl_cfg_entry_point")
def main(env_cfg: MySlamEnvCfg, agent_cfg: UnitreeGo2RoughPPORunnerCfg):
    """Run the reinforcement learning agent with SLAM."""
    
    # [FIX] 기본 설정 대신 커스텀 설정(MySlamEnvCfg) 사용
    if hasattr(env_cfg.commands, "base_velocity"):
        env_cfg.commands.base_velocity.resampling_time_range = (1.0e9, 1.0e9)
        env_cfg.commands.base_velocity.heading_command = False
        env_cfg.commands.base_velocity.debug_vis = False
        print("[INFO] MySlamEnvCfg 적용: Heading Command DISABLE, Resampling Infinite")

    # [FIX] 모델 크기 불일치 해결
    agent_cfg.policy.actor_hidden_dims = [128, 128, 128]
    agent_cfg.policy.critic_hidden_dims = [128, 128, 128]
    agent_cfg.policy.activation = "elu"
    
    # [DEBUG] ROS 2 환경 변수 확인
    print("\n" + "="*70)
    print("[DEBUG] ROS 2 환경 변수 확인")
    print("="*70)
    print(f"ROS_DOMAIN_ID: {os.environ.get('ROS_DOMAIN_ID', 'NOT SET')}")
    print(f"CYCLONEDDS_URI: {os.environ.get('CYCLONEDDS_URI', 'NOT SET')}")
    print(f"RMW_IMPLEMENTATION: {os.environ.get('RMW_IMPLEMENTATION', 'NOT SET')}")
    print(f"ROS_LOCALHOST_ONLY: {os.environ.get('ROS_LOCALHOST_ONLY', 'NOT SET')}")
    print("="*70 + "\n")

    # 1. World 생성 및 RL 환경 구축
    # 1. World 생성 및 RL 환경 구축
    # [FIX] 기본 설정 대신 MySlamEnvCfg 인스턴스 사용
    custom_env_cfg = MySlamEnvCfg()
    custom_env_cfg.scene.num_envs = args_cli.num_envs if args_cli.num_envs else 1

    env = gym.make("Isaac-Velocity-Rough-Unitree-Go2-Play-v0", cfg=custom_env_cfg)
    env = RslRlVecEnvWrapper(env)
    
    # 시뮬레이션 월드 인스턴스 가져오기
    world = World.instance()
    if world is None:
        print("[WARN] World.instance()가 None입니다. env.unwrapped.sim을 사용합니다.")
        world = env.unwrapped.sim
    
    # 2. [NEW] 환경 오브젝트 배치
    # 2. [NEW] 환경 오브젝트 배치 (USD 로드로 대체됨)
    # create_environment_objects(world) - 삭제됨
    
    # [FIX] TerrainImport 실패 시 직접 USD 로드 시도
    usd_path = "/home/jnu/isaac_ws/slam_env.usd"
    if os.path.exists(usd_path):
        try:
            add_reference_to_stage(usd_path, "/World/Environment")
            print(f"[INFO] '{usd_path}'를 '/World/Environment'로 직접 로드했습니다.")
        except Exception as e:
            print(f"[WARN] USD 직접 로드 실패: {e}")
    else:
        print(f"[WARN] USD 파일을 찾을 수 없습니다: {usd_path}")

    # 3. 로봇 접근
    prim_path = "/World/envs/env_0/Robot"
    
    try:
        # IsaacLab Scene에서 가져오기
        go2_robot = env.unwrapped.scene["robot"]
        print("[INFO] IsaacLab Scene에서 'robot' 객체를 성공적으로 가져왔습니다.")
    except (KeyError, AttributeError):
        print("[WARN] Scene에서 'robot'을 찾을 수 없습니다. 직접 Articulation 객체를 생성합니다.")
        prim_path = "/World/envs/env_0/Robot"
        go2_robot = Articulation(prim_path=f"{prim_path}/base", name="go2")
        if hasattr(world, "physics_sim_view"):
            go2_robot.initialize(world.physics_sim_view)
        else:
            go2_robot.initialize()

    # 4. 카메라 설정
    print("[INFO] RL 환경 내 로봇 카메라 설정")

    camera_prim_path = f"{prim_path}/base/front_cam"
    stage = world.stage

    camera_sensor = Camera(
        prim_path=camera_prim_path,
        resolution=(640, 480),
    )
    camera_sensor.initialize()
    camera_sensor.add_motion_vectors_to_frame()
    camera_sensor.add_distance_to_image_plane_to_frame()
    print("[INFO] 칵셔라 센서 초기화 완료")

    camera_sensor.set_local_pose(
        translation=np.array([0.3, 0.0, 0.2]),
        orientation=np.array([1.0, 0.0, 0.0, 0.0]),
    )

    # 4.5. 가상 스크린 구성
    screen_prim = setup_virtual_screen(world.stage, f"{prim_path}/VisualScreen")

    # 동적 텍스처 제공자 초기화
    texture_provider = ui.DynamicTextureProvider("ros_screen")
    texture_provider.set_bytes_data([0] * 4 * 16 * 16, [16, 16])

    # 5. ROS 2 및 UI 초기화
    print("\n" + "="*70)
    print("[DEBUG] ROS 2 초기화 시작")
    print("="*70)
    
    try:
        rclpy.init()
        print("[DEBUG] ✓ rclpy.init() 성공")
        print(f"[DEBUG] ✓ rclpy.ok() = {rclpy.ok()}")
    except Exception as e:
        print(f"[ERROR] ✗ rclpy.init() 실패: {e}")
        raise
    
    print("[DEBUG] Go2SlamPublisher 노드 생성 중...")
    slam_publisher = Go2SlamPublisher()
    slam_publisher.camera_sensor = camera_sensor
    slam_publisher.robot_prim = stage.GetPrimAtPath(prim_path)
    slam_publisher.screen_prim = screen_prim  # 가상 스크린 연결
    slam_publisher.texture_provider = texture_provider  # 텍스처 제공자 연결
    slam_publisher.articulation = go2_robot
    
    print("\n" + "-"*70)
    print("[DEBUG] ROS 2 Publisher 정보")
    print("-"*70)
    print(f"노드 이름: {slam_publisher.get_name()}")
    print(f"Image 토픽: {slam_publisher.image_pub.topic_name}")
    print(f"Camera Info 토픽: {slam_publisher.camera_info_pub.topic_name}")
    print(f"Odom 토픽: {slam_publisher.odom_pub.topic_name}")
    print(f"TF 토픽: {slam_publisher.tf_pub.topic_name}")
    print("-"*70 + "\n")

    ui_window, update_ui_func, ui_image_provider = setup_ui_window(camera_sensor)

    print("[DEBUG] ROS 2 Executor 시작 중...")
    executor = rclpy.executors.MultiThreadedExecutor()
    executor.add_node(slam_publisher)
    
    def spin_executor():
        try:
            print("[DEBUG] ✓ Executor 스레드 시작됨")
            executor.spin()
        except Exception as e:
            print(f"[ERROR] ✗ Executor 스레드 오류: {e}")
            import traceback
            traceback.print_exc()
    
    executor_thread = threading.Thread(target=spin_executor, daemon=True)
    executor_thread.start()
    print(f"[DEBUG] ✓ Executor 스레드 실행 중 (alive={executor_thread.is_alive()})")
    print("="*70 + "\n")

    # 6. 시뮬레이션 시작 및 루프
    world.reset()

    # [NEW] 카메라 및 GPU 버퍼 웜업
    print("[INFO] 카메라 및 GPU 버퍼 웜업 중 (30 steps)...")
    for i in range(30):
        world.step(render=True)
        simulation_app.update()
        if i % 10 == 0:
            print(f"Warmup step {i}/30...")
            
    # [NEW] OmniGraph 카메라 퍼블리셔 설정 호출
    cam_prim_path = f"{prim_path}/base/front_cam"
    try:
        setup_ros2_camera_graph(cam_prim_path)
    except Exception as e:
        print(f"[WARN] ROS2 OmniGraph 카메라 설정 실패: {e}")

    # [FIX] 로봇 초기 안정화
    valid_robot = False
    if hasattr(go2_robot, "is_valid"):
        if go2_robot.is_valid(): valid_robot = True
    else:
        valid_robot = True
        
    if valid_robot:
        device = env.unwrapped.device
        # Go2 표준 기립 자세
        standing_joints = torch.tensor([
            0.0, 0.8, -1.5, # FL
            0.0, 0.8, -1.5, # FR
            0.0, 0.8, -1.5, # RL
            0.0, 0.8, -1.5  # RR
        ], device=device, dtype=torch.float32)
        
        # 1. 관절 위치 설정
        if hasattr(go2_robot, "set_joint_positions"):
             go2_robot.set_joint_positions(standing_joints)
        elif hasattr(go2_robot, "write_joint_state_to_sim"):
             if standing_joints.ndim == 1:
                 standing_joints_2d = standing_joints.unsqueeze(0)
             else:
                 standing_joints_2d = standing_joints
             go2_robot.write_joint_state_to_sim(standing_joints_2d, torch.zeros_like(standing_joints_2d))

        # 2. 바닥에서 살짝 뜬 상태로 포즈 설정
        initial_root_pos = torch.tensor([0.0, 0.0, 0.4], device=device)
        initial_root_rot = torch.tensor([1.0, 0.0, 0.0, 0.0], device=device)
        
        if hasattr(go2_robot, "set_world_pose"):
            go2_robot.set_world_pose(
                position=initial_root_pos,
                orientation=initial_root_rot
            )
        elif hasattr(go2_robot, "write_root_state_to_sim"):
             root_state = torch.cat([initial_root_pos, initial_root_rot, torch.zeros(6, device=device)])
             if root_state.ndim == 1:
                 root_state = root_state.unsqueeze(0)
             go2_robot.write_root_state_to_sim(root_state)
            
        for _ in range(5):
            world.step(render=True)
        print("[DEBUG] 로봇 Standing Pose 설정 완료 (높이 0.4m)")

    # [NEW] RL 정책 로드
    task_name = "Isaac-Velocity-Flat-Unitree-Go2-v0" 
    resume_path = get_published_pretrained_checkpoint("rsl_rl", task_name)
    if resume_path is None:
        task_name = "Isaac-Velocity-Rough-Unitree-Go2-v0"
        resume_path = get_published_pretrained_checkpoint("rsl_rl", task_name)
    
    policy = None
    if resume_path is not None:
        print(f"[INFO] Loading RL policy from: {resume_path}")
        runner = OnPolicyRunner(env, agent_cfg.to_dict(), log_dir=None, device=agent_cfg.device)
        try:
            runner.load(resume_path)
            policy = runner.get_inference_policy(device=env.unwrapped.device)
            print("[INFO] RL policy loaded successfully.")
        except Exception as e:
            print(f"[ERROR] RL policy loading failed: {e}")
    else:
        print("[WARN] Pretrained checkpoint not found.")

    # [NEW] 키보드 컨트롤러 초기화
    keyboard = WasdKeyboard(Se2KeyboardCfg(v_x_sensitivity=0.8, v_y_sensitivity=0.8, omega_z_sensitivity=1.5))

    # [NEW] 초기 관측치 획득
    obs = env.get_observations()
    dt = env.unwrapped.step_dt

    step_count = 0
    publish_count = 0
    
    while simulation_app.is_running():
        start_loop_time = time.time()
        
        # [NEW] 키보드 입력 및 RL 추론
        vel_cmd = keyboard.advance()
        
        # 환경에 명령 전달
        if hasattr(env.unwrapped, "command_manager"):
            cmd_term = env.unwrapped.command_manager.get_term("base_velocity")
            if cmd_term is not None:
                # [FIX] Tensor/NumPy 호환성 처리
                if isinstance(vel_cmd, torch.Tensor):
                    vel_tensor = vel_cmd.to(device=env.unwrapped.device, dtype=torch.float32)
                else:
                    vel_tensor = torch.tensor(vel_cmd, device=env.unwrapped.device, dtype=torch.float32)

                # [FIX] Runtime Config Override (강제 적용) - 헤딩 커맨드 끄기
                if hasattr(cmd_term, "cfg"):
                    if hasattr(cmd_term.cfg, "heading_command") and cmd_term.cfg.heading_command:
                        cmd_term.cfg.heading_command = False
                        
                    if hasattr(cmd_term.cfg, "resampling_time_range") and cmd_term.cfg.resampling_time_range != (1.0e9, 1.0e9):
                        cmd_term.cfg.resampling_time_range = (1.0e9, 1.0e9)

                # 1. Command Buffer
                cmd_term.vel_command_b[:] = vel_tensor
                
                # 2. Command Actual
                if hasattr(cmd_term, "command"):
                    cmd_term.command[:] = vel_tensor

        # [FIX] policy가 로드된 경우에만 추론 실행
        if policy is not None:
            with torch.inference_mode():
                actions = policy(obs)
                obs, _, _, _ = env.step(actions)
        else:
            if hasattr(env.unwrapped, "sim"):
                env.unwrapped.sim.step(render=True)
            else:
                 env.sim.step(render=True)

        # [NEW] 로봇 상태 업데이트
        if hasattr(go2_robot, "data") and hasattr(go2_robot.data, "root_pos_w"):
            pos = go2_robot.data.root_pos_w
            ori = go2_robot.data.root_quat_w
        elif hasattr(go2_robot, "get_world_pose"):
            pos, ori = go2_robot.get_world_pose()
        else:
            pos = torch.zeros(3, device=env.unwrapped.device)
            ori = torch.tensor([1.0, 0.0, 0.0, 0.0], device=env.unwrapped.device)
            
        if hasattr(pos, "cpu"): pos = pos.cpu().numpy()
        if hasattr(ori, "cpu"): ori = ori.cpu().numpy()
        
        if pos.ndim == 2: pos = pos[0]
        if ori.ndim == 2: ori = ori[0]
        
        if step_count % 100 == 0:
             print(f"[DEBUG] Robot Pose: {pos}")

        slam_publisher.base_pos = pos
        slam_publisher.base_ori = ori
        slam_publisher.update_virtual_screen()

        # SLAM 데이터 발행
        slam_publisher.publish_data()
        publish_count += 1

        # UI 업데이트
        if update_ui_func:
            update_ui_func(texture_provider)

        step_count += 1
        if step_count % 100 == 0:
            print(f"[INFO] Step: {step_count} - World active")
            # [DEBUG] 로그
            print(f"[DEBUG] rclpy.ok(): {rclpy.ok()}")

    timeline = omni.timeline.get_timeline_interface()
    timeline.stop()
    rclpy.shutdown()
    simulation_app.close()


if __name__ == "__main__":
    main()
