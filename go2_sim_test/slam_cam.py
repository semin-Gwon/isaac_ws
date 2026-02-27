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
    print(
        "오류: 'isaaclab'을 찾을 수 없습니다. 'isaaclab' 콘다 환경에 있는지 확인하세요."
    )
    sys.exit(1)

# AppLauncher용 인자 파서 설정
parser = argparse.ArgumentParser(description="Go2 로봇 ROS 2 시각화")
# AppLauncher 기본 인자 추가 (headless 등)
AppLauncher.add_app_launcher_args(parser)
args_cli = parser.parse_args()

# omni.isaac.core 익스텐션 강제 활성화 (임포트 오류 방지)
if "--enable" not in sys.argv or "omni.isaac.core" not in sys.argv:
    sys.argv.append("--enable")
    sys.argv.append("omni.isaac.core")

# Isaac Sim 앱 실행
app_launcher = AppLauncher(args_cli)
simulation_app = app_launcher.app

# 앱 실행 후 모듈 임포트 (중요)
import carb
import omni
import omni.kit.commands
from omni.isaac.core import World
from omni.isaac.core.utils.extensions import enable_extension

enable_extension("omni.isaac.sensor")
from omni.isaac.core.articulations import Articulation
from omni.isaac.core.utils.stage import add_reference_to_stage
from omni.isaac.sensor import Camera
from pxr import Sdf, Gf, UsdGeom, UsdShade

# omni.ui 및 기타 모듈 안전 임포트
ui = None
try:
    import omni.ui as _ui

    ui = _ui
except Exception as e:
    carb.log_warn(f"omni.ui 임포트 실패: {e}. UI 시각화가 제한될 수 있습니다.")

# ROS 2 라이브러리
import rclpy
from rclpy.node import Node
from rclpy.qos import QoSProfile, ReliabilityPolicy, HistoryPolicy
from rclpy.callback_groups import ReentrantCallbackGroup
from nav_msgs.msg import Odometry
from geometry_msgs.msg import TransformStamped
from sensor_msgs.msg import CameraInfo

try:
    import tf2_ros
except ImportError:
    carb.log_warn("tf2_ros module not found. Using custom shim.")
    from tf2_msgs.msg import TFMessage
    from rclpy.qos import QoSProfile, DurabilityPolicy

    class ShimTransformBroadcaster:
        def __init__(self, node):
            self.node = node
            self.pub = node.create_publisher(TFMessage, "/tf", 10)

        def sendTransform(self, transform):
            msg = TFMessage()
            if not isinstance(transform, list):
                transform = [transform]
            msg.transforms = transform
            self.pub.publish(msg)

    class ShimStaticTransformBroadcaster:
        def __init__(self, node):
            self.node = node
            qos = QoSProfile(depth=1, durability=DurabilityPolicy.TRANSIENT_LOCAL)
            self.pub = node.create_publisher(TFMessage, "/tf_static", qos)

        def sendTransform(self, transform):
            msg = TFMessage()
            if not isinstance(transform, list):
                transform = [transform]
            msg.transforms = transform
            self.pub.publish(msg)

    class tf2_ros_shim:
        TransformBroadcaster = ShimTransformBroadcaster
        StaticTransformBroadcaster = ShimStaticTransformBroadcaster

    tf2_ros = tf2_ros_shim

# Unitree Go2 메시지 타입 임포트
try:
    from unitree_go.msg import LowState
except ImportError:
    carb.log_error(
        "unitree_go.msg.LowState를 임포트할 수 없습니다. 워크스페이스를 소싱했는지 확인하세요."
    )

    class LowState:
        pass


# 센서 메시지 타입 임포트
try:
    from sensor_msgs.msg import Image as RosImage, CompressedImage

    HAS_CAMERA = True
except ImportError:
    HAS_CAMERA = False
    carb.log_warn("sensor_msgs를 찾을 수 없어 카메라 시각화가 비활성화됩니다.")


class Go2Visualizer(Node):
    """
    ROS 2를 통해 Go2 로봇의 상태 및 카메라 데이터를 수신하여 Isaac Sim에 동기화하는 노드입니다.
    """

    def __init__(self, articulation: Articulation):
        super().__init__("go2_visualizer")
        self.articulation = articulation
        self.texture_provider = None
        self.ui_image_provider = None
        self.ui_depth_provider = None
        self.ui_window = None
        self.screen_prim = None  # 가상 스크린 참조
        self.camera_sensor = None  # 가상 카메라 센서 객체

        # ROS 2 발행자 추가
        self.camera_info_pub = self.create_publisher(
            CameraInfo, "/my_go2/color/camera_info", 10
        )
        if HAS_CAMERA:
            self.image_pub = self.create_publisher(
                RosImage, "/my_go2/color/image_raw", 10
            )
            self.depth_pub = self.create_publisher(
                RosImage, "/my_go2/depth/image_rect_raw", 10
            )
        else:
            self.image_pub = None
            self.depth_pub = None
        self.tf_broadcaster = tf2_ros.TransformBroadcaster(self)
        self.static_tf_broadcaster = tf2_ros.StaticTransformBroadcaster(self)

        # 로봇 상태 변수
        self.joint_positions = np.zeros(12)
        self.base_pos = np.array([0.0, 0.0, 0.3])
        self.base_ori = np.array([1.0, 0.0, 0.0, 0.0])  # w, x, y, z

        # UI 및 카메라 관련 변수 초기화
        self.latest_image: Optional[np.ndarray] = None
        self.latest_depth: Optional[np.ndarray] = None
        self.texture_provider: Optional[Any] = None
        self.ui_window: Optional[Any] = None
        self.ui_image_provider: Optional[Any] = None
        self.ui_depth_provider: Optional[Any] = None

        # QoS 프로파일 설정
        qos = QoSProfile(
            reliability=ReliabilityPolicy.BEST_EFFORT,
            history=HistoryPolicy.KEEP_LAST,
            depth=1,
        )
        qos_cam = QoSProfile(
            reliability=ReliabilityPolicy.BEST_EFFORT,
            history=HistoryPolicy.KEEP_LAST,
            depth=10,
        )

        # 병렬 처리를 위한 콜백 그룹
        self.cb_group = ReentrantCallbackGroup()

        # 카메라 구독 설정
        self.camera_topic = "/my_go2/color/image_raw/compressed"
        self.depth_topic = "/my_go2/depth/image_rect_raw"

        # 가상 스크린용 동적 텍스처 제공자 초기화
        if ui:
            self.texture_provider = ui.DynamicTextureProvider("ros_screen")
            self.texture_provider.set_bytes_data([0] * 4 * 16 * 16, [16, 16])

        if HAS_CAMERA:
            # RGB 카메라 구독
            self.create_subscription(
                CompressedImage,
                self.camera_topic,
                self.camera_callback,
                qos_cam,
                callback_group=self.cb_group,
            )
            print(f"RGB 카메라 토픽 구독 중: {self.camera_topic}")

            # Depth 카메라 구독
            try:
                # from sensor_msgs.msg import Image as RosImage # Removed to avoid shadowing global

                self.create_subscription(
                    RosImage,
                    self.depth_topic,
                    self.depth_callback,
                    qos_cam,
                    callback_group=self.cb_group,
                )
                print(f"Depth 카메라 토픽 구독 중: {self.depth_topic}")
            except Exception as e:
                carb.log_warn(f"Depth 구독 실패: {e}")

        # 관절 상태 및 오도메트리 구독
        self.create_subscription(
            LowState,
            "/lf/lowstate",
            self.joint_callback,
            qos,
            callback_group=self.cb_group,
        )
        self.create_subscription(
            Odometry,
            "/utlidar/robot_odom",
            self.odom_callback,
            qos,
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

    def joint_callback(self, msg: LowState) -> None:
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

    def camera_callback(self, msg: CompressedImage) -> None:
        """압축 이미지 메시지를 수신하여 처리합니다."""
        try:
            np_arr = np.frombuffer(msg.data, np.uint8)
            cv_image = cv2.imdecode(np_arr, cv2.IMREAD_COLOR)

            if cv_image is not None:
                # print(f"[DEBUG] RGB 콜백 호출됨 - shape: {cv_image.shape}")
                self.latest_image = cv_image

                # texture_provider 업데이트 (가상 스크린용)
                if self.texture_provider:
                    img_rgba = cv2.cvtColor(cv_image, cv2.COLOR_BGR2RGBA)
                    img_rgba[:, :, 3] = 255
                    data = img_rgba.flatten().tolist()
                    h, w = img_rgba.shape[:2]
                    self.texture_provider.set_bytes_data(data, [w, h])
        except Exception as e:
            carb.log_error(f"카메라 콜백 오류: {e}")
            print(f"[ERROR] RGB 콜백 오류: {e}")

    def depth_callback(self, msg) -> None:
        """Depth 이미지 메시지를 수신하여 처리합니다."""
        try:
            # 디버그: 콜백 호출 확인
            # print(f"[DEBUG] Depth 콜백 호출됨 - encoding: {msg.encoding}, size: {msg.height}x{msg.width}")

            # [수정] cv_bridge 제거 및 numpy 직접 파싱 (map_success.py 방식)
            # encoding 타입에 따라 dtype 결정
            dtype = np.uint16  # 기본값

            if msg.encoding == "mono8":
                dtype = np.uint8
            elif msg.encoding == "16UC1":
                dtype = np.uint16
            elif msg.encoding == "32FC1":
                dtype = np.float32

            # numpy로 직접 파싱
            depth_image = np.frombuffer(msg.data, dtype=dtype).reshape(
                msg.height, msg.width
            )

            # 시각화를 위한 정규화 및 컬러맵 적용
            # NaN 및 inf 값 처리
            depth_clean = np.nan_to_num(depth_image, nan=0.0, posinf=0.0, neginf=0.0)
            # 유효한 depth 범위로 클리핑
            if dtype == np.uint16:
                depth_clean = np.clip(depth_clean, 100, 10000)  # 0.1m ~ 10m (mm 단위)
            elif dtype == np.float32:
                depth_clean = np.clip(depth_clean, 0.1, 10.0)  # 0.1m ~ 10m (m 단위)
            # 정규화
            depth_min, depth_max = depth_clean.min(), depth_clean.max()
            if depth_max > depth_min:
                depth_normalized = (
                    (depth_clean - depth_min) / (depth_max - depth_min) * 255
                ).astype(np.uint8)
            else:
                depth_normalized = np.zeros_like(depth_clean, dtype=np.uint8)
            depth_colored = cv2.applyColorMap(depth_normalized, cv2.COLORMAP_TURBO)

            self.latest_depth = depth_colored
            # print(f"[DEBUG] Depth 이미지 처리 완료 - shape: {depth_colored.shape}")

        except Exception as e:
            carb.log_error(f"Depth 콜백 오류: {e}")
            print(f"[ERROR] Depth 콜백 오류: {e}")

    def update_robot(self) -> None:
        """Isaac Sim 내의 로봇 관절 및 포즈를 업데이트합니다."""
        self.articulation.set_joint_positions(
            self.joint_positions, joint_indices=self.get_joint_indices()
        )
        self.articulation.set_world_pose(self.base_pos, self.base_ori)

        # 가상 스크린 위치 업데이트
        self.update_virtual_screen()

    def get_joint_indices(self) -> list:
        """Articulation 내의 관절 인덱스를 매핑합니다."""
        if not hasattr(self, "_joint_indices"):
            all_joints = self.articulation.dof_names
            indices = [
                all_joints.index(name)
                for name in self.joint_names
                if name in all_joints
            ]
            self._joint_indices = indices
        return self._joint_indices

    def publish_camera_data(self, camera_prim_path: str) -> None:
        """가상 카메라의 정보를 가져와 CameraInfo 및 TF를 발행합니다."""
        try:
            # 1. 정적 TF 발행 (base_link -> camera_link)
            # 실제 로봇의 위치에 맞게 조정 (앞쪽 상단)
            static_tf = TransformStamped()
            static_tf.header.stamp = self.get_clock().now().to_msg()
            static_tf.header.frame_id = "base_link"
            static_tf.child_frame_id = "camera_link"
            static_tf.transform.translation.x = 0.25
            static_tf.transform.translation.y = 0.0
            static_tf.transform.translation.z = 0.1
            static_tf.transform.rotation.w = 1.0
            self.static_tf_broadcaster.sendTransform(static_tf)

            # 2. 광학 좌표계 TF 발행 (camera_link -> camera_optical_frame)
            # ROS 표준: Z축이 전방, X축이 오른쪽, Y축이 아래
            optical_tf = TransformStamped()
            optical_tf.header.stamp = static_tf.header.stamp
            optical_tf.header.frame_id = "camera_link"
            optical_tf.child_frame_id = "camera_optical_frame"
            # 90도 회전 (Quat: x=0.5, y=-0.5, z=0.5, w=-0.5 등)
            optical_tf.transform.rotation.x = -0.5
            optical_tf.transform.rotation.y = 0.5
            optical_tf.transform.rotation.z = -0.5
            optical_tf.transform.rotation.w = 0.5
            self.static_tf_broadcaster.sendTransform(optical_tf)

            # 3. Camera Info 발행
            # Isaac Sim의 기본 카메라 파라미터 (640x480 기준 가상값)
            ci = CameraInfo()
            ci.header.stamp = static_tf.header.stamp
            ci.header.frame_id = "camera_optical_frame"
            ci.width = 640
            ci.height = 480
            ci.distortion_model = "plumb_bob"
            ci.d = [0.0, 0.0, 0.0, 0.0, 0.0]
            # K matrix (f_x, 0, c_x, 0, f_y, c_y, 0, 0, 1)
            ci.k = [500.0, 0.0, 320.0, 0.0, 500.0, 240.0, 0.0, 0.0, 1.0]
            # R matrix (identity)
            ci.r = [1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0]
            # P matrix
            ci.p = [500.0, 0.0, 320.0, 0.0, 0.0, 500.0, 240.0, 0.0, 0.0, 0.0, 1.0, 0.0]

            self.camera_info_pub.publish(ci)

            # 4. 이미지 데이터 발행 (Isaac Sim 센서에서 가져오기)
            if self.camera_sensor:
                # RGB 이미지
                rgba_data = self.camera_sensor.get_rgba()
                if rgba_data is not None:
                    rgb_msg = RosImage()
                    rgb_msg.header.stamp = ci.header.stamp
                    rgb_msg.header.frame_id = "camera_optical_frame"
                    rgb_msg.height, rgb_msg.width = 480, 640
                    rgb_msg.encoding = "rgb8"
                    rgb_msg.is_bigendian = False
                    rgb_msg.step = 640 * 3
                    # RGBA -> RGB 변환 및 bytes 변환
                    rgb_data = rgba_data[:, :, :3].astype(np.uint8)
                    rgb_msg.data = rgb_data.tobytes()
                    self.image_pub.publish(rgb_msg)

                # Depth 이미지
                depth_data = self.camera_sensor.get_depth()
                if depth_data is not None:
                    depth_msg = RosImage()
                    depth_msg.header.stamp = ci.header.stamp
                    depth_msg.header.frame_id = "camera_optical_frame"
                    depth_msg.height, depth_msg.width = 480, 640
                    depth_msg.encoding = "32FC1"  # 또는 '16UC1'
                    depth_msg.is_bigendian = False
                    depth_msg.step = 640 * 4  # float32 = 4 bytes
                    depth_msg.data = depth_data.astype(np.float32).tobytes()
                    self.depth_pub.publish(depth_msg)

        except Exception as e:
            carb.log_warn(f"카메라 데이터 발행 오류: {e}")

    def setup_ui_window(self) -> None:
        """Isaac Sim 내부에 카메라 피드를 표시할 UI 창을 생성합니다."""
        try:
            import omni.ui as ui

            # [수정] ByteImageProvider 사용 (face_cam.py 방식)
            self.ui_image_provider = ui.ByteImageProvider()
            self.ui_depth_provider = ui.ByteImageProvider()

            print("[DEBUG] UI Providers 생성 완료")

            # UI 창 생성 (RGB와 Depth를 나란히 표시)
            self.ui_window = ui.Window("Go2 Camera Feeds", width=1280, height=520)
            with self.ui_window.frame:
                with ui.VStack(spacing=5):
                    # 헤더
                    ui.Label(
                        "실시간 카메라 피드", height=20, alignment=ui.Alignment.CENTER
                    )

                    # RGB와 Depth를 나란히 배치
                    with ui.HStack(spacing=10):
                        # RGB 카메라
                        with ui.VStack():
                            ui.Label(
                                "RGB Camera", height=20, alignment=ui.Alignment.CENTER
                            )
                            ui.ImageWithProvider(
                                self.ui_image_provider, width=640, height=480
                            )

                        # Depth 카메라
                        with ui.VStack():
                            ui.Label(
                                "Depth Camera", height=20, alignment=ui.Alignment.CENTER
                            )
                            ui.ImageWithProvider(
                                self.ui_depth_provider, width=640, height=480
                            )

            print("[DEBUG] UI 창 생성 완료")
        except Exception as e:
            carb.log_warn(f"UI 창 생성 실패: {e}")
            print(f"[ERROR] UI 창 생성 실패: {e}")

    def update_ui_image(self) -> None:
        """UI 창의 이미지를 업데이트합니다. (Isaac Sim 칵셔라 데이터 사용)"""
        # Isaac Sim 낶부 칵셔라에서 직접 데이터 가져오기
        if self.camera_sensor is None:
            return

        try:
            # RGB 이미지 업데이트
            rgba_data = self.camera_sensor.get_rgba()
            if rgba_data is not None and self.ui_image_provider:
                # RGBA -> RGBA (convert to list for UI)
                h, w = rgba_data.shape[:2]
                img_rgba = rgba_data.astype(np.uint8)
                data = img_rgba.flatten().tolist()
                self.ui_image_provider.set_bytes_data(data, [w, h])

            # Depth 이미지 업데이트
            depth_data = self.camera_sensor.get_depth()
            if depth_data is not None and self.ui_depth_provider:
                # NaN 및 inf 값 처리
                depth_clean = np.nan_to_num(depth_data, nan=0.0, posinf=0.0, neginf=0.0)
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
                depth_colored = cv2.applyColorMap(depth_normalized, cv2.COLORMAP_TURBO)
                depth_rgba = cv2.cvtColor(depth_colored, cv2.COLOR_BGR2RGBA)
                h, w = depth_rgba.shape[:2]
                data = depth_rgba.flatten().tolist()
                self.ui_depth_provider.set_bytes_data(data, [w, h])

        except Exception as e:
            carb.log_error(f"UI 이미지 업데이트 오류: {e}")
            print(f"[ERROR] UI 업데이트 오류: {e}")

    def update_virtual_screen(self) -> None:
        """가상 스크린을 로봇의 현재 위치에 따라 업데이트합니다."""
        if self.screen_prim is None:
            return

        try:
            from pxr import Gf
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
            carb.log_warn(f"가상 스크린 업데이트 오류: {e}")


def setup_virtual_screen(
    stage: Sdf.Layer, screen_path: str = "/World/Go2/VisualScreen"
) -> None:
    """
    영상 시각화를 위한 가상 스크린 평면과 자체 발광 재질을 생성합니다.
    """
    print(f"가상 스크린 생성 중: {screen_path}")

    # 평면 메쉬 생성 (YZ 평면 상에 생성하여 전방X를 향하도록 설정)
    plane = UsdGeom.Mesh.Define(stage, screen_path)
    h_w, h_h = 0.2, 0.15
    # 로봇을 마주보도록 정점 정의
    points = [
        Gf.Vec3f(0, -h_w, -h_h),
        Gf.Vec3f(0, h_w, -h_h),
        Gf.Vec3f(0, h_w, h_h),
        Gf.Vec3f(0, -h_w, h_h),
    ]
    plane.CreatePointsAttr(points)
    plane.CreateFaceVertexCountsAttr([4])
    plane.CreateFaceVertexIndicesAttr([0, 1, 2, 3])

    # 테스처 좌표 (UV) 설정 - UsdGeom.PrimvarsAPI 사용
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


def main() -> None:
    """메인 실행 함수입니다."""
    world = World(stage_units_in_meters=1.0)
    world.scene.add_default_ground_plane()

    # 로봇 모델 로드
    urdf_path = "/home/jnu/go2_ws/src/go2_description/urdf/go2_description.urdf"
    enable_extension("isaacsim.asset.importer.urdf")

    from isaacsim.asset.importer.urdf import _urdf

    import_config = _urdf.ImportConfig()
    import_config.merge_fixed_joints = False
    import_config.fix_base = True
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

    # 가상 카메라 센서 추가
    cam_path = f"{prim_path}/base_link/camera"
    camera_sensor = Camera(
        prim_path=cam_path,
        resolution=(640, 480),
        orientation=np.array([1.0, 0.0, 0.0, 0.0]),
    )
    camera_sensor.initialize()
    # Camera data collection activation
    camera_sensor.add_motion_vectors_to_frame()
    camera_sensor.add_distance_to_image_plane_to_frame()  # for depth
    print("[INFO] Camera sensor initialized")

    # 가상 스크린 구성
    screen_prim = setup_virtual_screen(world.stage, f"{prim_path}/VisualScreen")

    # ROS 2 초기화
    if "CYCLONEDDS_URI" not in os.environ:
        os.environ["CYCLONEDDS_URI"] = "file:///home/jnu/isaac_ws/cyclonedds.xml"

    rclpy.init()
    visualizer = Go2Visualizer(go2_robot)

    # 스크린 및 카메라 센서 참조 저장
    visualizer.screen_prim = screen_prim
    visualizer.camera_sensor = camera_sensor

    executor = rclpy.executors.MultiThreadedExecutor()
    executor.add_node(visualizer)

    spin_thread = threading.Thread(target=executor.spin, daemon=True)
    spin_thread.start()

    world.reset()

    # UI 창 설정
    visualizer.setup_ui_window()

    # 시뮬레이션 루프
    try:
        while simulation_app.is_running():
            world.step(render=True)
            if go2_robot.handles_initialized:
                visualizer.update_robot()
                # 카메라 데이터 발행 (SLAM용)
                visualizer.publish_camera_data(f"{prim_path}/base_link/camera")

            # UI 창 이미지 업데이트
            visualizer.update_ui_image()
    except Exception as e:
        print(f"루프 실행 중 오류: {e}")
    finally:
        executor.shutdown()
        visualizer.destroy_node()
        rclpy.shutdown()
        simulation_app.close()


if __name__ == "__main__":
    main()
