import argparse
import sys
import os
import time
import threading
import numpy as np
import cv2

# [필수] Isaac Lab AppLauncher 임포트
try:
    from isaaclab.app import AppLauncher
except ImportError:
    print("오류: 'isaaclab' 패키지를 찾을 수 없습니다.")
    sys.exit(1)

# AppLauncher용 인자 파서 설정
parser = argparse.ArgumentParser(description="Go2 SLAM RoboStack (World Class Version)")
parser.add_argument("--task", type=str, default="Isaac-Velocity-Flat-Unitree-Go2-Play-v0", help="Task name.")
parser.add_argument("--seed", type=int, default=None, help="Random seed.")

# [NEW] go2_sim.py 참조: --rt 같은 추가 인자가 필요하면 여기서 처리
# (지금은 cli_args 모듈이 없으므로 직접 추가한 task 인자만 사용)

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
# 앱 실행 후 모듈 임포트 (중요)
# ============================================================================
import carb
import omni
import omni.kit.commands
from pxr import Usd, UsdGeom, Gf, Sdf, UsdShade, UsdPhysics
import omni.ui as ui
from omni.isaac.core.utils.stage import add_reference_to_stage
from omni.isaac.core.utils.prims import get_prim_at_path
from omni.isaac.core import World
from omni.isaac.core.utils.extensions import enable_extension

# [FIX] 확장 로드 보장
enable_extension("isaacsim.ros2.bridge")
simulation_app.update() # 중요: 확장이 로드되려면 최소 1회 업데이트 필요

enable_extension("omni.isaac.sensor")
from omni.isaac.sensor import Camera
from omni.isaac.core.articulations import Articulation
from omni.isaac.core.objects import (
    VisualCuboid,
    VisualSphere,
    VisualCone,
    VisualCylinder,
)

# [FIX] 외부 RMW 설정 제거 (Isaac Sim 내부 기본값 사용 유도)
# 사용자 터미널의 RMW_IMPLEMENTATION 설정이 Isaac Sim과 충돌할 수 있음
os.environ.pop("RMW_IMPLEMENTATION", None)

# ROS 2 import (RoboStack 환경)
import rclpy
from rclpy.node import Node
from sensor_msgs.msg import Image, CameraInfo
from nav_msgs.msg import Odometry
from geometry_msgs.msg import TransformStamped, Quaternion, Pose, Twist
from tf2_msgs.msg import TFMessage
from std_msgs.msg import Header

# [NEW] Isaac Lab RL 및 관련 임포트 (go2_sim.py 참조)
import torch
import gymnasium as gym
from isaaclab_rl.rsl_rl import RslRlVecEnvWrapper
from isaaclab.devices import Se2Keyboard, Se2KeyboardCfg
from isaaclab.utils.pretrained_checkpoint import get_published_pretrained_checkpoint
from rsl_rl.runners import OnPolicyRunner

# [NEW] OmniGraph 및 Viewport 관련 임포트
import omni.graph.core as og
from isaacsim.core.utils import extensions
from omni.kit.viewport.utility import create_viewport_window

# [NEW] ROS2 bridge 확장 활성화 (go2_sim.py 참조)
extensions.enable_extension("isaacsim.ros2.bridge")
from isaaclab_tasks.utils.hydra import hydra_task_config # Hydra 활성화
from isaaclab.utils import configclass # [FIX] @configclass 데코레이터 임포트 추가
import isaaclab_tasks  # noqa

# Go2 전용 설정 임포트 (Flat 환경 적용)
from isaaclab_tasks.manager_based.locomotion.velocity.config.go2.flat_env_cfg import UnitreeGo2FlatEnvCfg_PLAY
from isaaclab_tasks.manager_based.locomotion.velocity.config.go2.agents.rsl_rl_ppo_cfg import UnitreeGo2RoughPPORunnerCfg

# Unitree Go2 메시지 타입 임포트 (slam_cam.py 방식)
try:
    from unitree_go.msg import LowState

    HAS_LOWSTATE = True
except ImportError:
    print(
        "unitree_go.msg.LowState를 임포트할 수 없습니다. 관절 상태 수신이 비활성화됩니다."
    )
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
        from rclpy.qos import QoSProfile, ReliabilityPolicy, HistoryPolicy

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

        # 콜백 그룹
        from rclpy.callback_groups import ReentrantCallbackGroup

        self.cb_group = ReentrantCallbackGroup()

        self.joint_positions = [0.0] * 12
        self.base_pos = np.zeros(3)
        self.base_ori = np.array([1.0, 0.0, 0.0, 0.0])

        # [FIX] 구독(Subscription) 로직 제거 - 시뮬레이션이 Ground Truth임
        # 시뮬레이션이 ROS 상태를 따르는 것이 아니라, 시뮬레이션 상태를 ROS로 보냄.
        # 따라서 Joint/Odom 구독자 제거하여 로봇 제어 간섭(후진 문제) 해결.

    def update_robot(self) -> None:
        """가상 스크린만 업데이트합니다 (로봇 제어 권한 제거)."""
        # [FIX] Articulation 수동 조작 코드 제거 (RL이 제어)
        self.update_virtual_screen()

        # 가상 스크린 위치 업데이트
        self.update_virtual_screen()

    def update_virtual_screen(self) -> None:
        """가상 스크린을 로봇의 현재 위치에 따라 업데이트합니다."""
        if self.screen_prim is None:
            return

        try:
            import math

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
                indices = [
                    all_joints.index(name)
                    for name in self.joint_names
                    if name in all_joints
                ]
                self._joint_indices = indices
            else:
                return []
        return self._joint_indices

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
                    
                    # 최종 발행 데이터 체크 (선택적)
                    # self.get_logger().info(f"발행 데이터 바이트 크기: {len(msg.data)}")
                    
                    self.image_pub.publish(msg)

                    # Camera Info 발행
                    info_msg = CameraInfo()
                    info_msg.header = msg.header
                    info_msg.height = msg.height
                    info_msg.width = msg.width
                    self.camera_info_pub.publish(info_msg)
                else:
                    # self.get_logger().warn("Camera output is None")
                    pass

            except Exception as e:
                self.get_logger().error(f"Error publishing camera data: {e}")

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


def create_environment_objects(world):
    """World 클래스를 사용하여 환경 오브젝트 생성"""
    if not hasattr(world, "scene"):
        print("[INFO] world 객체에 scene이 없습니다. 직접 프림을 생성합니다.")
        from omni.isaac.core.objects import VisualCuboid, VisualSphere, VisualCapsule
        
        return
    # scene이 있는 경우 기존 방식 유지
    # 1. 빨간색 큐브
    world.scene.add(
        VisualCuboid(
            prim_path="/World/Cube",
            name="cube",
            position=np.array([2.5, 0.0, 0.5]),
            scale=np.array([0.5, 0.5, 0.5]),
            color=np.array([1.0, 0.0, 0.0]),
        )
    )
    # 2. 파란색 구
    world.scene.add(
        VisualSphere(
            prim_path="/World/Sphere",
            name="sphere",
            position=np.array([0.0, 2.5, 0.5]),
            radius=0.5,
            color=np.array([0.0, 0.0, 1.0]),
        )
    )
    # 3. 초록색 캡슐
    world.scene.add(
        VisualCapsule(
            prim_path="/World/Capsule",
            name="capsule",
            position=np.array([-2.5, 0.0, 0.5]),
            radius=0.4,
            height=1.0,
            color=np.array([0.0, 1.0, 0.0]),
        )
    )

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
    # 1. 빨간색 큐브
    world.scene.add(
        VisualCuboid(
            prim_path="/World/Cube",
            name="cube",
            position=np.array([2.5, 0.0, 0.5]),
            scale=np.array([0.5, 0.5, 0.5]),
            color=np.array([1.0, 0.0, 0.0]),
        )
    )

    # 2. 파란색 구
    world.scene.add(
        VisualSphere(
            prim_path="/World/Sphere",
            name="sphere",
            position=np.array([2.5, 1.0, 0.5]),
            scale=np.array([0.5, 0.5, 0.5]),
            color=np.array([0.0, 0.0, 1.0]),
        )
    )

    # 3. 초록색 원뿔 - VisualCone 사용 (5.1에서 경고 뜨지만 동작함, 혹은 UsdGeom 사용)
    # 여기서는 안전하게 UsdGeom 사용 + Xform
    stage = world.stage
    cone_path = "/World/Cone"
    UsdGeom.Cone.Define(stage, cone_path)
    cone_prim = stage.GetPrimAtPath(cone_path)
    if cone_prim.IsValid():
        xform = UsdGeom.XformCommonAPI(cone_prim)
        xform.SetTranslate(Gf.Vec3d(2.5, -1.0, 0.5))
        xform.SetScale(Gf.Vec3f(0.5, 0.5, 0.5))
        # 색상 적용
        gprim = UsdGeom.Gprim(cone_prim)
        if not gprim.GetDisplayColorAttr().IsValid():
            gprim.CreateDisplayColorAttr()
        gprim.GetDisplayColorAttr().Set([Gf.Vec3f(0.0, 1.0, 0.0)])

    # 조명 추가
    omni.kit.commands.execute(
        "CreatePrim", prim_path="/World/Light", prim_type="DistantLight"
    )
    light_prim = stage.GetPrimAtPath("/World/Light")
    if light_prim.IsValid():
        light_prim.GetAttribute("inputs:intensity").Set(1000.0)

    print("[DEBUG] 환경 오브젝트 및 조명 배치 완료")
    return agent_cfg

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
def main(env_cfg: UnitreeGo2FlatEnvCfg_PLAY, agent_cfg: UnitreeGo2RoughPPORunnerCfg):
    """Run the reinforcement learning agent with SLAM."""
    
    # [FIX] 기본 설정 대신 커스텀 설정(MySlamEnvCfg) 사용
    # hydra가 주입한 env_cfg를 무시하고 직접 생성하거나, 속성을 덮어씀
    # 여기서는 속성을 덮어쓰는 방식이 hydra 흐름상 안전함
    if hasattr(env_cfg.commands, "base_velocity"):
        env_cfg.commands.base_velocity.resampling_time_range = (1.0e9, 1.0e9)
        env_cfg.commands.base_velocity.heading_command = False
        print("[INFO] MySlamEnvCfg 적용: Heading Command DISABLE, Resampling Infinite")

    # [중요] 시뮬레이션 앱 설정
    # Flat(평지) 환경 설정을 가져오기
    # env_cfg = UnitreeGo2FlatEnvCfg_PLAY() # This line is now handled by hydra
    # agent_cfg = UnitreeGo2RoughPPORunnerCfg() # This line is now handled by hydra
    
    # [FIX] 모델 크기 불일치 해결 (Checkpoint: [128, 128, 128] vs Current: [512, 256, 128])
    agent_cfg.policy.actor_hidden_dims = [128, 128, 128]
    agent_cfg.policy.critic_hidden_dims = [128, 128, 128]
    agent_cfg.policy.activation = "elu" # 보통 elu를 사용함
    
    # [NEW] 속도 기반 제어를 위해 heading_command 비활성화 (지속 회전 가능)
    # [FIX] RL 정책이 Heading 제어를 기대하는 것 같으므로 기본값(True)을 유지하고
    # 메인 루프에서 Yaw 적분기를 통해 지속 회전하도록 수정함.
    # if hasattr(env_cfg.commands, "base_velocity"):
    #     env_cfg.commands.base_velocity.heading_command = False
    #     print("[INFO] Heading command disabled - using direct angular velocity (ang_vel_z)")
    # [DEBUG] ROS 2 환경 변수 확인
    # ========================================================================
    print("\n" + "="*70)
    print("[DEBUG] ROS 2 환경 변수 확인")
    print("="*70)
    print(f"ROS_DOMAIN_ID: {os.environ.get('ROS_DOMAIN_ID', 'NOT SET')}")
    print(f"CYCLONEDDS_URI: {os.environ.get('CYCLONEDDS_URI', 'NOT SET')}")
    print(f"RMW_IMPLEMENTATION: {os.environ.get('RMW_IMPLEMENTATION', 'NOT SET')}")
    print(f"ROS_LOCALHOST_ONLY: {os.environ.get('ROS_LOCALHOST_ONLY', 'NOT SET')}")
    print("="*70 + "\n")

    # 1. World 생성 및 RL 환경 구축
    # [NEW] RL 환경 설정 (Hydra 없이 직접 설정)
    env_cfg.scene.num_envs = 1
    
    # [IMPORTANT] 카메라 활성화를 위해 설정 확인
    # 필요시 여기서 env_cfg.scene.robot.sensors 등을 조정할 수 있음

    env = gym.make("Isaac-Velocity-Rough-Unitree-Go2-Play-v0", cfg=env_cfg)
    env = RslRlVecEnvWrapper(env)
    # 시뮬레이션 월드 인스턴스 가져오기 (scene 속성 접근을 위해 World 클래스 사용)
    from omni.isaac.core import World
    world = World.instance()
    if world is None:
        print("[WARN] World.instance()가 None입니다. env.unwrapped.sim을 사용합니다.")
        world = env.unwrapped.sim
    
    # 2. [NEW] 환경 오브젝트 배치 (기존 slam_go2.py 로직 복구)
    create_environment_objects(world)

    # 3. 로봇 접근 (IsaacLab Scene 활용)
    # env.make()가 이미 로봇을 생성하고 관리함.
    prim_path = "/World/envs/env_0/Robot" # IsaacLab 기본 경로 (env_0 기준)
    
    try:
        # IsaacLab Scene에서 가져오기 (물리 업데이트 동기화)
        # 주의: scene["robot"]은 [num_envs, ...] 형태의 데이터를 다루는 래퍼일 수 있음
        go2_robot = env.unwrapped.scene["robot"]
        print("[INFO] IsaacLab Scene에서 'robot' 객체를 성공적으로 가져왔습니다.")
    except (KeyError, AttributeError):
        print("[WARN] Scene에서 'robot'을 찾을 수 없습니다. 직접 Articulation 객체를 생성합니다.")
        prim_path = "/World/envs/env_0/Robot" # IsaacLab 기본 경로
        go2_robot = Articulation(prim_path=f"{prim_path}/base", name="go2")
        # env.reset() 등을 통해 이미 초기화되었을 수 있으나, 명시적 초기화 시도
        if hasattr(world, "physics_sim_view"):
            go2_robot.initialize(world.physics_sim_view)
        else:
            go2_robot.initialize()

    # 4. 카메라 설정 (env 내부 로봇의 프림 경로 사용)
    print("[INFO] RL 환경 내 로봇 카메라 설정")

    # IsaacLab Go2 환경의 카메라 프림 경로 (보통 front_cam 등으로 정의됨)
    camera_prim_path = f"{prim_path}/base/front_cam"
    stage = world.stage

    # 카메라 센서 객체 생성 (이미 존재할 수도 있으나 Camera 클래스로 래핑)
    camera_sensor = Camera(
        prim_path=camera_prim_path,
        resolution=(640, 480),
    )
    camera_sensor.initialize()
    # 칵셔라 데이터 수집 활성화
    camera_sensor.add_motion_vectors_to_frame()
    camera_sensor.add_distance_to_image_plane_to_frame()  # depth용
    print("[INFO] 칵셔라 센서 초기화 완료")

    # 칵셔라 위치 조정
    camera_sensor.set_local_pose(
        translation=np.array([0.3, 0.0, 0.2]),
        orientation=np.array([1.0, 0.0, 0.0, 0.0]),
    )

    # 4.5. 가상 스크린 구성 (slam_cam.py 스타일)
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
    # [NEW] Articulation 객체 연결
    slam_publisher.articulation = go2_robot
    
    # [DEBUG] Publisher 정보 출력
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

    # [NEW] 카메라 및 GPU 버퍼 웜업 (중요: 초기 검은 화면 방지)
    # 모델 로드 전에 화면부터 띄우기 위해 순서 변경
    # [NEW] 카메라 및 GPU 버퍼 웜업 (중요: 초기 검은 화면 방지)
    # 모델 로드 전에 화면부터 띄우기 위해 순서 변경
    print("[INFO] 카메라 및 GPU 버퍼 웜업 중 (30 steps)...")
    for i in range(30):
        world.step(render=True)
        simulation_app.update()
        if i % 10 == 0:
            print(f"Warmup step {i}/30...")
            
    # [NEW] OmniGraph 카메라 퍼블리셔 설정 호출 (RTAB-Map용)
    cam_prim_path = f"{prim_path}/base/front_cam"
    try:
        setup_ros2_camera_graph(cam_prim_path)
    except Exception as e:
        print(f"[WARN] ROS2 OmniGraph 카메라 설정 실패: {e}")

    # [FIX] 로봇 초기 안정화 (추락 및 붕괴 방지)
    # IsaacLab 로봇 객체는 is_valid()가 없을 수 있음.
    valid_robot = False
    if hasattr(go2_robot, "is_valid"):
        if go2_robot.is_valid(): valid_robot = True
    else:
        # IsaacLab 객체라면 기본적으로 유효하다고 가정
        valid_robot = True
        
    if valid_robot:
        device = env.unwrapped.device
        # Go2 표준 기립 자세 (Hip, Thigh, Calf 순서)
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
             # IsaacLab 방식 (2차원 필요)
             if standing_joints.ndim == 1:
                 standing_joints_2d = standing_joints.unsqueeze(0)
             else:
                 standing_joints_2d = standing_joints
             go2_robot.write_joint_state_to_sim(standing_joints_2d, torch.zeros_like(standing_joints_2d))

        # 2. 바닥에서 살짝 뜬 상태로 포즈 설정 (0.4m가 적당)
        initial_root_pos = torch.tensor([0.0, 0.0, 0.4], device=device)
        initial_root_rot = torch.tensor([1.0, 0.0, 0.0, 0.0], device=device)
        
        if hasattr(go2_robot, "set_world_pose"):
            go2_robot.set_world_pose(
                position=initial_root_pos,
                orientation=initial_root_rot
            )
        elif hasattr(go2_robot, "write_root_state_to_sim"):
             # IsaacLab 방식 (root_state: [pos, quat, lin_vel, ang_vel])
             root_state = torch.cat([initial_root_pos, initial_root_rot, torch.zeros(6, device=device)])
             # (num_envs, 13) 형태 필요
             if root_state.ndim == 1:
                 root_state = root_state.unsqueeze(0)
             go2_robot.write_root_state_to_sim(root_state)
            
        # 물리 엔진에 즉시 반영
        for _ in range(5):
            world.step(render=True)
        print("[DEBUG] 로봇 Standing Pose 설정 완료 (높이 0.4m)")

    # [NEW] RL 정책 로드 (이전 편집에서 누락된 부분 복구)
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

    # [NEW] 키보드 컨트롤러 초기화 (회전 감도 조정 - ang_vel_z 기준)
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
                # [FIX] Tensor/NumPy 호환성 처리 (TypeError 해결)
                if isinstance(vel_cmd, torch.Tensor):
                    cmd_val = vel_cmd.cpu().numpy()
                else:
                    cmd_val = vel_cmd
                
                # [DEBUG] 키 입력 확인을 위한 출력
                if np.any(np.abs(cmd_val) > 0.01):
                    # cmd_val은 이제 확실히 numpy 배열임
                    pass
                    # print(f"[DEBUG] 키보드 명령 - X: {cmd_val[0]:.2f}, Y: {cmd_val[1]:.2f}, Yaw: {cmd_val[2]:.2f}")
                
                # [FIX] 텐서 생성 최적화 및 경고 방지
                if not isinstance(vel_cmd, torch.Tensor):
                    vel_tensor = torch.tensor(vel_cmd, device=env.unwrapped.device, dtype=torch.float32)
                else:
                    vel_tensor = vel_cmd.to(device=env.unwrapped.device, dtype=torch.float32)
                
                # [FIX] Runtime Config Override (강제 적용)
                # 초기화 시 설정이 씹히는 경우를 대비해 매 프레임 강제 적용
                if hasattr(cmd_term, "cfg"):
                    if hasattr(cmd_term.cfg, "heading_command") and cmd_term.cfg.heading_command:
                        cmd_term.cfg.heading_command = False
                        print("[INFO] Runtime: Heading Command 강제 비활성화 (Velocity Mode 전환)")
                    
                    if hasattr(cmd_term.cfg, "resampling_time_range") and cmd_term.cfg.resampling_time_range != (1.0e9, 1.0e9):
                        cmd_term.cfg.resampling_time_range = (1.0e9, 1.0e9)
                        print("[INFO] Runtime: Resampling Time 무한대로 설정")

                # [FIX] Yaw Integrator 제거 (Velocity Control 모드 사용)
                # go2_sim.py와 동일하게 직접 속도 명령 전달 (Buffer 및 Command 모두 기록)
                
                # 1. Command Buffer (Resampling 소스)
                cmd_term.vel_command_b[:] = vel_tensor
                
                # 2. Command Actual (Observation 소스)
                if hasattr(cmd_term, "command"):
                    cmd_term.command[:] = vel_tensor

                # [DEBUG] 입력 확인 (회전 명령이 있을 때만)
                if np.abs(cmd_val[2]) > 0.01:
                     pass
                     # print(f"[DEBUG] Velocity Input: {cmd_val[2]:.2f}")

                # [FIX] cmd_term이 compute()에서 vel_command_b를 사용할 수도 있고 Resample할 수도 있음
                # 안전을 위해 command 버퍼에도 직접 기록 시도
                if hasattr(cmd_term, "command"):
                    cmd_term.command[:] = vel_tensor

                cmd_term.vel_command_b[:] = vel_tensor
                
                # [DEBUG] Command 적용 확인
                # if step_count % 50 == 0:
                #     print(f"[DEBUG] Applied Command: {cmd_term.command[0].cpu().numpy()}")

        # [FIX] policy가 로드된 경우에만 추론 실행
        if policy is not None:
            with torch.inference_mode():
                actions = policy(obs)
                obs, _, _, _ = env.step(actions)
                
                # [DEBUG] Policy 실행 확인 (100번마다 한 번)
                if step_count % 50 == 0:
                     pass
                     # print(f"[DEBUG] Policy Step: Action mean={actions.mean():.4f}")
                     # print(f"[DEBUG] Command Buffer After Step: {cmd_term.command[0].cpu().numpy()}")

        else:
            # Policy 실패 시에도 물리 시뮬레이션은 돌아야 함 (Visual Debugging)
            # 액션 없이 step하거나 world.step() 호출
            # env.step(torch.zeros(...))을 하면 에러 날 수 있으므로 world.step()
            # print("[WARN] Policy is None! Robot will collapse.")
            if hasattr(env.unwrapped, "sim"):
                env.unwrapped.sim.step(render=True)
            else:
                 # IsaacLab 이전 버전 호환성
                 env.sim.step(render=True)

        # [NEW] 로봇 상태 업데이트 (Articulation 객체 수동 업데이트 불필요, RL이 제어 중)
        # 하지만 가상 스크린 등은 로봇 위치를 따라가야 함
        # [FIX] RL 로봇의 위치 정보 업데이트 (ROS/UI를 위해 numpy 변환 및 차원 축소)
        # 객체 타입에 따라 포즈 획득 방식 분기
        if hasattr(go2_robot, "data") and hasattr(go2_robot.data, "root_pos_w"):
            # IsaacLab Articulation
            pos = go2_robot.data.root_pos_w
            ori = go2_robot.data.root_quat_w
        elif hasattr(go2_robot, "get_world_pose"):
            # omni.isaac.core.articulations.Articulation
            pos, ori = go2_robot.get_world_pose()
        else:
            # Fallback (가상 스크린 업데이트 실패 방지)
            pos = torch.zeros(3, device=env.unwrapped.device)
            ori = torch.tensor([1.0, 0.0, 0.0, 0.0], device=env.unwrapped.device)
            
        if hasattr(pos, "cpu"): pos = pos.cpu().numpy()
        if hasattr(ori, "cpu"): ori = ori.cpu().numpy()
        
        # (1, 3) -> (3,) 차원 축소 (Robo가 1개인 경우)
        if pos.ndim == 2: pos = pos[0]
        if ori.ndim == 2: ori = ori[0]
        
        # [DEBUG] 로봇 위치 로그 (100번마다)
        if step_count % 100 == 0:
             print(f"[DEBUG] Robot Pose: {pos}")

        slam_publisher.base_pos = pos
        slam_publisher.base_ori = ori
        slam_publisher.update_virtual_screen()

        # SLAM 데이터 발행
        slam_publisher.publish_data()
        publish_count += 1

        # UI 업데이트 (가상 스크린 텍스처 포함)
        if update_ui_func:
            update_ui_func(texture_provider)

        step_count += 1
        if step_count % 100 == 0:
            print(f"[INFO] Step: {step_count} - World active")
            # [DEBUG] 주기적으로 토픽 발행 상태 확인
            print(f"[DEBUG] 토픽 발행 횟수: {publish_count}")
            print(f"[DEBUG] Image 구독자 수: {slam_publisher.image_pub.get_subscription_count()}")
            print(f"[DEBUG] Executor 스레드 상태: {executor_thread.is_alive()}")
            print(f"[DEBUG] rclpy.ok(): {rclpy.ok()}")

    timeline = omni.timeline.get_timeline_interface()
    timeline.stop()
    rclpy.shutdown()
    simulation_app.close()


if __name__ == "__main__":
    main()
