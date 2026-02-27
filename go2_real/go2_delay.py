import argparse
import sys
import os
import time
import numpy as np

# [DDS 환경 설정] Isaac Sim 앱 실행 전에 반드시 설정해야 합니다.
# CycloneDDS를 사용하여 브리지 서버(ros2_bridge_server.py)와 로컬 통신합니다.
os.environ.setdefault('RMW_IMPLEMENTATION', 'rmw_cyclonedds_cpp')
_script_dir = os.path.dirname(os.path.abspath(__file__))
_isaacsim_xml = os.path.join(_script_dir, 'cyclonedds_isaacsim.xml')
if os.path.exists(_isaacsim_xml):
    os.environ['CYCLONEDDS_URI'] = f'file://{_isaacsim_xml}'
    print(f"[DDS 설정] RMW=rmw_cyclonedds_cpp, URI={_isaacsim_xml}")
else:
    print(f"[DDS 경고] {_isaacsim_xml} 파일이 없습니다. 로컬 DDS 통신이 안 될 수 있습니다.")
os.environ.setdefault('ROS_DOMAIN_ID', '0')
print(f"[DDS 설정] ROS_DOMAIN_ID={os.environ.get('ROS_DOMAIN_ID', '0')}")

# [필수] Isaac Lab AppLauncher 임포트
try:
    from isaaclab.app import AppLauncher
except ImportError:
    print(
        "오류: 'isaaclab'을 찾을 수 없습니다. 'isaaclab' 콘다 환경에 있는지 확인하세요."
    )
    sys.exit(1)

# AppLauncher용 인자 파서 설정
parser = argparse.ArgumentParser(description="Go2 로봇 ROS 2 시각화 (OmniGraph 기반)")
AppLauncher.add_app_launcher_args(parser)
args_cli = parser.parse_args()

# Isaac Sim 앱 실행
app_launcher = AppLauncher(args_cli)
simulation_app = app_launcher.app

import carb
import omni
import omni.graph.core as og
import omni.kit.commands
import usdrt

# Isaac Sim 5.x: ROS2 브릿지만 명시적으로 활성화 (core, sensor는 자동 로드됨)
from isaacsim.core.utils import extensions

extensions.enable_extension("isaacsim.ros2.bridge")
simulation_app.update()

# 추가 필수 모듈 (Isaac Sim 5.x)
from isaacsim.core.api import World
from isaacsim.core.prims import Articulation
from isaacsim.core.utils.stage import add_reference_to_stage
from pxr import Sdf, UsdGeom, UsdShade, Gf


class Go2Visualizer:
    """
    Isaac Sim OmniGraph를 사용하여 ROS 2 브리지로부터 데이터를 직접 수신하는 시각화 클래스입니다.
    rclpy를 전혀 사용하지 않으며, 모든 데이터 흐름은 OmniGraph 내에서 처리됩니다.
    """

    def __init__(self, articulation: Articulation, stage, prim_path: str):
        self.articulation = articulation
        self.stage = stage
        self.prim_path = prim_path
        self.screen_prim = None
        self.joint_graph_path = "/World/ROS2_Joint_Graph"
        self.pose_graph_path = "/World/ROS2_Pose_Graph"

        # UI 관련
        self._last_joint_diag_time = 0.0
        self._last_tf_diag_time = 0.0
        self._last_joint_stamp = 0.0
        self._last_pose_pos = None
        self._printed_name_diag = False

        # OmniGraph 설정
        self.setup_joint_graph()
        self.setup_pose_graph()

    def setup_joint_graph(self):
        """
        /joint_states 토픽을 구독하여 로봇의 Articulation Controller에 직접 연결합니다.
        """
        print("[INFO] OmniGraph JointState 수신 설정 중...")
        keys = og.Controller.Keys
        og.Controller.edit(
            {
                "graph_path": self.joint_graph_path,
                "evaluator_name": "execution",
                "pipeline_stage": og.GraphPipelineStage.GRAPH_PIPELINE_STAGE_SIMULATION,
            },
            {
                keys.CREATE_NODES: [
                    ("OnPlaybackTick", "omni.graph.action.OnPlaybackTick"),
                    ("subJointState", "isaacsim.ros2.bridge.ROS2SubscribeJointState"),
                    (
                        "artController",
                        "isaacsim.core.nodes.IsaacArticulationController",
                    ),
                ],
                keys.CONNECT: [
                    ("OnPlaybackTick.outputs:tick", "subJointState.inputs:execIn"),
                    ("OnPlaybackTick.outputs:tick", "artController.inputs:execIn"),
                    (
                        "subJointState.outputs:jointNames",
                        "artController.inputs:jointNames",
                    ),
                    (
                        "subJointState.outputs:positionCommand",
                        "artController.inputs:positionCommand",
                    ),
                ],
                keys.SET_VALUES: [
                    ("subJointState.inputs:topicName", "/joint_states"),
                    ("subJointState.inputs:queueSize", 10),
                    ("artController.inputs:targetPrim", [usdrt.Sdf.Path(self.prim_path)]),
                ],
            },
        )
        print(f"[INFO] JointState 그래프 설정 완료 (targetPrim={self.prim_path})")

    def debug_joint_subscription(self):
        """ROS2SubscribeJointState 노드 수신 상태를 주기적으로 출력합니다."""
        now = time.time()
        if now - self._last_joint_diag_time < 1.0:
            return
        self._last_joint_diag_time = now

        try:
            stamp_attr = og.Controller.attribute(
                f"{self.joint_graph_path}/subJointState.outputs:timeStamp"
            )
            name_attr = og.Controller.attribute(
                f"{self.joint_graph_path}/subJointState.outputs:jointNames"
            )

            stamp = float(stamp_attr.get()) if stamp_attr else 0.0
            joint_names = name_attr.get() if name_attr else []
            joint_count = len(joint_names) if joint_names is not None else 0

            if stamp > 0.0:
                if (not self._printed_name_diag) and hasattr(self.articulation, "dof_names"):
                    dof_names = list(self.articulation.dof_names)
                    recv_names = list(joint_names) if joint_names is not None else []
                    overlap = len(set(dof_names).intersection(set(recv_names)))
                    print(
                        f"[ROS2] DOF={len(dof_names)}, recv_names={len(recv_names)}, name_overlap={overlap}"
                    )
                    self._printed_name_diag = True
                if stamp != self._last_joint_stamp:
                    print(
                        f"[ROS2] /joint_states 수신 중: stamp={stamp:.3f}, joints={joint_count}"
                    )
                    self._last_joint_stamp = stamp
            else:
                print("[ROS2] 아직 /joint_states 수신 없음 (stamp=0.0)")
        except Exception as e:
            print(f"[ROS2] 구독 진단 실패: {e}")

    def setup_pose_graph(self):
        """
        /tf(odom -> base_link)를 구독하여 로봇 base pose를 stage에 반영합니다.
        """
        print("[INFO] OmniGraph TF 수신 설정 중...")
        keys = og.Controller.Keys
        base_prim = f"{self.prim_path}/base_link"

        og.Controller.edit(
            {
                "graph_path": self.pose_graph_path,
                "evaluator_name": "execution",
                "pipeline_stage": og.GraphPipelineStage.GRAPH_PIPELINE_STAGE_SIMULATION,
            },
            {
                keys.CREATE_NODES: [
                    ("OnPlaybackTick", "omni.graph.action.OnPlaybackTick"),
                    ("subTF", "isaacsim.ros2.bridge.ROS2SubscribeTransformTree"),
                ],
                keys.CONNECT: [
                    ("OnPlaybackTick.outputs:tick", "subTF.inputs:execIn"),
                ],
                keys.SET_VALUES: [
                    ("subTF.inputs:topicName", "/tf"),
                    ("subTF.inputs:queueSize", 10),
                    # Map world root to odom and articulation base link to base_link frame.
                    ("subTF.inputs:frameNamesMap", [self.prim_path, "odom", base_prim, "base_link"]),
                    # OgnROS2SubscribeTransformTree requires a prim with PhysxArticulationRootAPI.
                    # In this URDF setup, base_link prim is the articulation root.
                    ("subTF.inputs:articulationRoots", [base_prim]),
                ],
            },
        )
        print(
            f"[INFO] TF 그래프 설정 완료 (topic=/tf, root={base_prim}, map={self.prim_path}<->odom)"
        )

    def debug_tf_and_pose(self):
        """실제 articulation 포즈 변화를 주기적으로 출력합니다."""
        now = time.time()
        if now - self._last_tf_diag_time < 1.0:
            return
        self._last_tf_diag_time = now

        try:
            pos_batch, ori_batch = self.articulation.get_world_poses()
            if hasattr(pos_batch, "tolist"):
                pos_batch = pos_batch.tolist()
            if hasattr(ori_batch, "tolist"):
                ori_batch = ori_batch.tolist()
            pos = pos_batch[0]
            ori = ori_batch[0]

            # wxyz
            w, x, y, z = ori[0], ori[1], ori[2], ori[3]
            roll = np.degrees(np.arctan2(2.0 * (w * x + y * z), 1.0 - 2.0 * (x * x + y * y)))
            pitch = np.degrees(np.arcsin(np.clip(2.0 * (w * y - z * x), -1.0, 1.0)))
            if self._last_pose_pos is None:
                moved = 0.0
            else:
                dx = pos[0] - self._last_pose_pos[0]
                dy = pos[1] - self._last_pose_pos[1]
                dz = pos[2] - self._last_pose_pos[2]
                moved = float(np.sqrt(dx * dx + dy * dy + dz * dz))
            self._last_pose_pos = [pos[0], pos[1], pos[2]]
            print(
                f"[ROS2] pose=({pos[0]:.3f}, {pos[1]:.3f}, {pos[2]:.3f}), dpos={moved:.4f} m, roll={roll:.1f}, pitch={pitch:.1f}"
            )
        except Exception as e:
            print(f"[ROS2] TF/pose 진단 실패: {e}")

    def update_virtual_screen(self):
        """가상 스크린을 로봇의 현재 위치에 따라 업데이트합니다."""
        if self.screen_prim is None:
            return

        try:
            # 로봇의 현재 위치와 방향 (OmniGraph에 의해 업데이트된 Articulation 상태)
            # get_world_pose()는 최신 Physics 스텝의 상태를 반환함
            pos_batch, ori_batch = self.articulation.get_world_poses()
            pos = pos_batch[0]
            ori = ori_batch[0]

            # Isaac Sim API returns numpy array usually
            if hasattr(pos, "tolist"):
                pos = pos.tolist()
            if hasattr(ori, "tolist"):
                ori = ori.tolist()

            w, x, y, z = ori[0], ori[1], ori[2], ori[3]

            import math

            # Yaw 각도 추출 (Z축 회전)
            yaw = math.atan2(2.0 * (w * z + x * y), 1.0 - 2.0 * (y * y + z * z))

            forward_x = math.cos(yaw)
            forward_y = math.sin(yaw)

            screen_offset_forward = 0.5
            screen_offset_up = 0.2

            screen_x = pos[0] + forward_x * screen_offset_forward
            screen_y = pos[1] + forward_y * screen_offset_forward
            screen_z = pos[2] + screen_offset_up

            xform = UsdGeom.Xformable(self.screen_prim)

            # Translate Op 존재 확인 및 설정
            translate_op = None
            rotate_op = None

            ops = xform.GetOrderedXformOps()
            if len(ops) > 0:
                translate_op = ops[0]
            else:
                translate_op = xform.AddTranslateOp()

            translate_op.Set(Gf.Vec3d(screen_x, screen_y, screen_z))

            if len(ops) > 1:
                rotate_op = ops[1]
            else:
                rotate_op = xform.AddRotateZOp()

            rotate_op.Set(math.degrees(yaw + math.pi))

        except Exception as e:
            pass  # 초기화 중이거나 에러 발생 시 무시


def setup_virtual_screen(stage, screen_path="/World/Go2/VisualScreen"):
    """
    영상 시각화를 위한 가상 스크린 평면과 자체 발광 재질을 생성합니다.
    """
    print(f"가상 스크린 생성 중: {screen_path}")

    plane = UsdGeom.Mesh.Define(stage, screen_path)
    h_w, h_h = 0.2, 0.15
    points = [
        Gf.Vec3f(0, -h_w, -h_h),
        Gf.Vec3f(0, h_w, -h_h),
        Gf.Vec3f(0, h_w, h_h),
        Gf.Vec3f(0, -h_w, h_h),
    ]
    plane.CreatePointsAttr(points)
    plane.CreateFaceVertexCountsAttr([4])
    plane.CreateFaceVertexIndicesAttr([0, 1, 2, 3])

    primvars_api = UsdGeom.PrimvarsAPI(plane)
    st_primvar = primvars_api.CreatePrimvar(
        "st", Sdf.ValueTypeNames.TexCoord2fArray, UsdGeom.Tokens.varying
    )
    st_primvar.Set([(0, 1), (1, 1), (1, 0), (0, 0)])

    xform = UsdGeom.Xformable(plane)
    xform.AddTranslateOp().Set(Gf.Vec3d(0.5, 0.0, 0.2))
    xform.AddRotateZOp().Set(0.0)  # 회전 Op 미리 추가

    mat_path = "/World/Looks/ScreenMat"
    material = UsdShade.Material.Define(stage, mat_path)
    shader = UsdShade.Shader.Define(stage, f"{mat_path}/PBRShader")
    shader.CreateIdAttr("UsdPreviewSurface")
    material.CreateSurfaceOutput().ConnectToSource(shader.ConnectableAPI(), "surface")
    # 카메라 구독 제거: 고정색 재질 사용
    shader.CreateInput("diffuseColor", Sdf.ValueTypeNames.Color3f).Set(
        Gf.Vec3f(0.05, 0.65, 0.65)
    )
    shader.CreateInput("emissiveColor", Sdf.ValueTypeNames.Color3f).Set(
        Gf.Vec3f(0.05, 0.65, 0.65)
    )

    UsdShade.MaterialBindingAPI(plane).Bind(material)
    return plane


def main():
    world = World(stage_units_in_meters=1.0)
    world.scene.add_default_ground_plane()

    # Go2 로봇 로드
    urdf_path = "/home/jnu/go2_ws/src/go2_description/urdf/go2_description.urdf"

    # URDF Importer 설정
    from isaacsim.asset.importer.urdf import _urdf

    import_config = _urdf.ImportConfig()
    import_config.merge_fixed_joints = False
    import_config.fix_base = False
    import_config.make_default_prim = True
    import_config.self_collision = False
    import_config.create_physics_scene = True
    import_config.import_inertia_tensor = False
    import_config.distance_scale = 1.0

    dest_path = "/tmp/go2.usd"
    omni.kit.commands.execute(
        "URDFParseAndImportFile",
        urdf_path=urdf_path,
        import_config=import_config,
        dest_path=dest_path,
    )

    prim_path = "/World/Go2"
    add_reference_to_stage(usd_path=dest_path, prim_path=prim_path)

    go2_robot = Articulation(prim_paths_expr=prim_path, name="go2")
    world.scene.add(go2_robot)

    # 가상 스크린 생성
    screen_prim = setup_virtual_screen(world.stage, f"{prim_path}/VisualScreen")

    # Visualizer (OmniGraph Controller) 초기화
    visualizer = Go2Visualizer(go2_robot, world.stage, prim_path)
    visualizer.screen_prim = screen_prim

    world.reset()
    # 초기 프레임에서 지면 충돌로 눕는 현상 방지용 초기 포즈
    go2_robot.set_world_poses(
        np.array([[0.0, 0.0, 0.32]], dtype=np.float32),
        np.array([[1.0, 0.0, 0.0, 0.0]], dtype=np.float32),
    )
    print(f"[INFO] Articulation prim path: {prim_path}")
    if hasattr(go2_robot, "dof_names"):
        print(f"[INFO] DOF 개수: {len(go2_robot.dof_names)}")

    print("[INFO] Isaac Sim 시뮬레이션 시작 (OmniGraph Mode)")
    print("[INFO] 실행 중... (멈추려면 터미널에서 Ctrl+C)")

    while simulation_app.is_running():
        world.step(render=True)
        visualizer.debug_joint_subscription()
        visualizer.debug_tf_and_pose()
        if go2_robot._is_initialized:
            visualizer.update_virtual_screen()

    simulation_app.close()


if __name__ == "__main__":
    main()
