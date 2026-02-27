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
AppLauncher.add_app_launcher_args(parser)
args_cli = parser.parse_args()

# omni.isaac.core 익스텐션 강제 활성화 (임포트 오류 방지)
if "--enable" not in sys.argv:
    sys.argv.append("--enable")
    sys.argv.append("omni.isaac.core")

# Isaac Sim 앱 실행
app_launcher = AppLauncher(args_cli)
simulation_app = app_launcher.app

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

enable_extension("omni.isaac.sensor")
from omni.isaac.sensor import Camera
from omni.isaac.core.articulations import Articulation
from omni.isaac.core.objects import (
    VisualCuboid,
    VisualSphere,
    VisualCone,
    VisualCylinder,
)

# ROS 2 import (RoboStack 환경)
import rclpy
from rclpy.node import Node
from sensor_msgs.msg import Image, CameraInfo
from nav_msgs.msg import Odometry
from geometry_msgs.msg import TransformStamped, Quaternion, Pose, Twist
from tf2_msgs.msg import TFMessage
from std_msgs.msg import Header

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

        # 관절 상태 및 오도메트리 구독
        if HAS_LOWSTATE:
            self.create_subscription(
                LowState,
                "/lf/lowstate",
                self.joint_callback,
                sensor_qos,
                callback_group=self.cb_group,
            )
        else:
            self.get_logger().warn(
                "Unitree LowState 메시지 타입을 찾을 수 없어 관절 상태 구독을 건너뜁니다."
            )

        self.create_subscription(
            Odometry,
            "/utlidar/robot_odom",
            self.odom_callback,
            sensor_qos,
            callback_group=self.cb_group,
        )

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

    def joint_callback(self, msg) -> None:
        """관절 상태 메시지를 수신하여 현재 위치를 업데이트합니다."""
        if hasattr(msg, "motor_state"):
            for i in range(12):
                self.joint_positions[i] = msg.motor_state[i].q

    def odom_callback(self, msg: Odometry) -> None:
        """오도메트리 메시지를 수신하여 로봇의 세계 좌표계 상 포즈를 업데이트합니다."""
        p = msg.pose.pose.position
        q = msg.pose.pose.orientation
        self.base_pos = np.array([p.x, p.y, p.z])
        self.base_ori = np.array([q.w, q.x, q.y, q.z])

    def update_robot(self) -> None:
        """Isaac Sim 내의 로봇 관절 및 포즈를 업데이트합니다."""
        if self.articulation:
            # IsaacLab 객체 호환성 체크
            if hasattr(self.articulation, "set_joint_positions"):
                # 관절 위치 업데이트
                self.articulation.set_joint_positions(
                    self.joint_positions, joint_indices=self.get_joint_indices()
                )
                # 베이스 포즈 업데이트
                self.articulation.set_world_pose(self.base_pos, self.base_ori)
            elif hasattr(self.articulation, "write_joint_state_to_sim"):
                 # IsaacLab: 여기서는 구현 생략 (RL이 제어하므로 Publisher가 강제 제어하면 충돌 가능)
                 pass

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
            translate_op = xform.GetOrderedXformOps()[0]  # 첫 번째 translate op
            translate_op.Set(Gf.Vec3d(screen_x, screen_y, screen_z))

            # 스크린 회전 업데이트 (로봇을 향하도록)
            if len(xform.GetOrderedXformOps()) < 2:
                # 회전 op가 없으면 추가
                rotate_op = xform.AddRotateZOp()
            else:
                rotate_op = xform.GetOrderedXformOps()[1]

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


def main():
    # ========================================================================
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

    # 1. World 생성
    world = World(stage_units_in_meters=1.0)
    world.scene.add_default_ground_plane()

    # 2. 환경 오브젝트 배치
    create_environment_objects(world)

    # 3. 로봇 로드 설정
    urdf_path = "/home/jnu/go2_ws/src/go2_description/urdf/go2_description.urdf"
    prim_path = "/World/Go2"

    from isaacsim.core.utils.extensions import enable_extension

    enable_extension("isaacsim.asset.importer.urdf")
    from isaacsim.asset.importer.urdf import _urdf

    import_config = _urdf.ImportConfig()
    import_config = _urdf.ImportConfig()
    import_config.merge_fixed_joints = False
    # [FIX] slam_cam.py 방식: 베이스를 고정하고 Kinematic하게 위치 제어 (넘어짐 방지)
    import_config.fix_base = True
    import_config.make_default_prim = True
    import_config.self_collision = False
    import_config.create_physics_scene = True
    import_config.import_inertia_tensor = False
    import_config.distance_scale = 1.0
    import_config.density = 0.0

    usd_dir = "/home/jnu/isaac_ws/generated"
    if not os.path.exists(usd_dir):
        os.makedirs(usd_dir)
    dest_path = os.path.join(usd_dir, "go2.usd")

    # URDF 파싱 및 임포트
    omni.kit.commands.execute(
        "URDFParseAndImportFile",
        urdf_path=urdf_path,
        import_config=import_config,
        dest_path=dest_path,
    )
    add_reference_to_stage(usd_path=dest_path, prim_path=prim_path)

    # Articulation으로 로봇 등록
    go2_robot = Articulation(prim_path=prim_path, name="go2")
    world.scene.add(go2_robot)

    # 초기 위치 설정
    go2_robot.set_world_pose(position=np.array([-2.0, 0.0, 0.4]))
    print("[DEBUG] 로봇 초기 위치 설정 완료: (-2.0, 0.0, 0.4)")

    # 4. 카메라 설정 (Camera 클래스 사용 - 경고 무시)
    print("[INFO] Camera 클래스를 사용하여 카메라 생성")

    camera_name = "RobotCamera"
    camera_parent = f"{prim_path}/base_link"

    stage = world.stage

    # base_link 확인 및 Fallback
    parent_prim = stage.GetPrimAtPath(camera_parent)
    if not parent_prim.IsValid():
        print(f"[WARN] {camera_parent}가 존재하지 않습니다. 'base'로 시도합니다.")
        if stage.GetPrimAtPath(f"{prim_path}/base").IsValid():
            camera_parent = f"{prim_path}/base"
        else:
            print("[ERROR] 카메라를 부착할 부모 링크를 찾을 수 없습니다.")

    camera_prim_path = f"{camera_parent}/{camera_name}"

    # Camera 객체 생성
    camera_sensor = Camera(
        prim_path=camera_prim_path,
        resolution=(640, 480),
        orientation=np.array([1.0, 0.0, 0.0, 0.0]),
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
    print("[INFO] 카메라 및 GPU 버퍼 웜업 중 (30 steps)...")
    for i in range(30):
        world.step(render=True)
        simulation_app.update()
        if i % 10 == 0:
            print(f"Warmup step {i}/30...")

    # [NEW] Standing Pose 설정 및 관절 제어 (Reset 후 적용)
    # 사용자의 요청으로 slam_cam.py 방식(초기값 0, ROS 제어 대기)으로 변경함.
    # 기존 Standing Pose 및 PD 제어 코드는 제거.

    # 초기 위치 0으로 설정 (slam_cam.py 스타일)
    if go2_robot.is_valid():
        go2_robot.set_joint_positions(np.zeros(go2_robot.num_dof))
        # [FIX] 초기 위치 설정 (0.3m)
        # fix_base=True 상태이므로 이 위치에 고정됨 (ROS Odom 수신 전까지)
        go2_robot.set_world_pose(position=np.array([0.0, 0.0, 0.3]))
        # go2_robot.set_joint_position_targets(np.zeros(go2_robot.num_dof)) # 필요시 추가
        print("[DEBUG] 로봇 관절 초기화 (0.0) 및 위치 고정 (z=0.3)")

    step_count = 0
    publish_count = 0
    
    while simulation_app.is_running():
        world.step(render=True)

        # [NEW] 로봇 상태 업데이트 (ROS 토픽 수신 시 반영)
        slam_publisher.update_robot()

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
