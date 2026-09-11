import os
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, LogInfo, ExecuteProcess, SetEnvironmentVariable
from launch.conditions import IfCondition, UnlessCondition
from launch.substitutions import LaunchConfiguration, PythonExpression
from launch_ros.actions import Node

_THIS_DIR = os.path.dirname(os.path.realpath(__file__))
_MAPS_DIR = os.path.join(_THIS_DIR, "maps")
_RTABMAP_DB_PATH = os.path.join(_MAPS_DIR, "rtabmap_real.db")

def generate_launch_description():
    os.makedirs(_MAPS_DIR, exist_ok=True)
    # 1) 런치 인자
    use_sim_time = LaunchConfiguration('use_sim_time', default='false')
    use_viz = LaunchConfiguration('use_viz', default='false')
    odom_eval_mode = LaunchConfiguration('odom_eval_mode', default='false')
    use_lidar = LaunchConfiguration('use_lidar', default='true')

    # 2) 전역 시간/TF 동기화 노드
    # 참고: 로봇 측 타임스탬프를 현재 시간축에 맞춰 재발행하고 TF 트리 연결성을 유지한다.
    # 참고: bash -c에서 PYTHONPATH를 정리하고 ROS 2/워크스페이스 환경을 소싱한 뒤 실행한다.
    topic_sync = ExecuteProcess(
        cmd=[
            'bash',
            '-c',
            [
                'mkdir -p /tmp/ros_logs; unset PYTHONPATH; source /opt/ros/humble/setup.bash; '
                'source /home/jnu/isaac_ws/install/local_setup.bash; '
                '/usr/bin/python3 /home/jnu/isaac_ws/go2_real/go2_topic_sync.py --odom-eval-mode ',
                odom_eval_mode,
                ' --use-lidar ',
                use_lidar,
            ],
        ],
        output='screen'
    )

    # 참고: Base Link -> Camera Optical Frame 연결은 topic_sync.py에서 처리됨.
    # 참고: Depth 이미지 재발행은 제거됨(실제 로봇은 Raw Depth 직접 전송).

    base_slam_parameters = {
        'map_frame_id': 'map',
        'odom_frame_id': 'odom',
        'frame_id': 'camera_link',
        'publish_tf': True,
        'subscribe_depth': True,
        'subscribe_rgb': True,
        'subscribe_imu': True,
        'subscribe_scan': False,
        'approx_sync': True,
        'approx_sync_max_interval': 0.3,
        'use_sim_time': use_sim_time,
        'queue_size': 50,
        'topic_queue_size': 20,
        'sync_queue_size': 50,
        'wait_for_transform': 1.0,
        'qos_image': 2,
        'qos_camera_info': 2,
        'Rtabmap/DetectionRate': '1.0',
        'Rtabmap/LoopClosureReextractFeatures': 'true',
        'Rtabmap/ImageBufferSize': '1',
        'Reg/Strategy': '0',
        'Vis/EstimationType': '2',
        'Reg/Force3DoF': 'false',
        'RGBD/OptimizeMaxError': '6.0',
        'Vis/MinInliers': '15',
        'Vis/InlierDistance': '0.08',
        'Mem/RehearsalSimilarity': '0.40',
        'Mem/NotLinkedNodesKept': 'false',
        'Cloud/NoiseFilteringRadius': '0.05',
        'Cloud/NoiseFilteringMinNeighbors': '5',
        'Cloud/VoxelSize': '0.015',
        'cloud_decimation': 1,
        'cloud_max_depth': 4.0,
        'cloud_min_depth': 0.2,
        'Grid/FromDepth': 'true',
        'Grid/3D': 'false',
        'Grid/RangeMax': '4.0',
        'Grid/MinClusterSize': '20',
        'Grid/MaxGroundHeight': '0.15',
        'Grid/MaxObstacleHeight': '2.0',
        'Grid/RayTracing': 'true',
        'database_path': _RTABMAP_DB_PATH,
    }
    base_viz_parameters = {
        'map_frame_id': 'map',
        'odom_frame_id': 'odom',
        'frame_id': 'camera_link',
        'publish_tf': True,
        'subscribe_depth': True,
        'subscribe_rgb': True,
        'subscribe_imu': True,
        'subscribe_scan': False,
        'subscribe_odom_info': False,
        'approx_sync': True,
        'approx_sync_max_interval': 0.3,
        'use_sim_time': use_sim_time,
        'queue_size': 50,
        'topic_queue_size': 20,
        'sync_queue_size': 50,
        'wait_for_transform': 1.0,
        'qos_image': 2,
        'qos_camera_info': 2,
        'Rtabmap/LoopClosureReextractFeatures': 'true',
        'Rtabmap/ImageBufferSize': '1',
        'Reg/Strategy': '0',
        'Vis/EstimationType': '2',
        'Reg/Force3DoF': 'false',
        'RGBD/OptimizeMaxError': '6.0',
        'Vis/MinInliers': '15',
        'Vis/InlierDistance': '0.08',
        'Mem/RehearsalSimilarity': '0.40',
        'Cloud/NoiseFilteringRadius': '0.05',
        'Cloud/NoiseFilteringMinNeighbors': '5',
        'Cloud/VoxelSize': '0.015',
        'cloud_decimation': 1,
        'cloud_max_depth': 4.0,
        'cloud_min_depth': 0.2,
        'Grid/FromDepth': 'true',
        'Grid/3D': 'false',
        'Grid/RangeMax': '4.0',
        'Grid/MinClusterSize': '20',
        'Grid/MaxGroundHeight': '0.15',
        'Grid/MaxObstacleHeight': '2.0',
        'Grid/RayTracing': 'true',
    }
    base_remappings = [
        ('rgb/image', '/my_go2/color/image_raw_sync'),
        ('depth/image', '/my_go2/depth/image_rect_raw_sync'),
        ('rgb/camera_info', '/my_go2/color/camera_info_sync'),
        ('imu', '/utlidar/imu_sync'),
        ('odom', '/utlidar/robot_odom_sync'),
    ]
    rgbd_cloud = Node(
        package='rtabmap_util',
        executable='point_cloud_xyzrgb',
        name='rgbd_cloud',
        output='screen',
        parameters=[{
            'decimation': 1,
            'voxel_size': 0.0,
            'min_depth': 0.2,
            'max_depth': 4.0,
            'approx_sync': True,
            'queue_size': 30,
            'noise_filter_radius': 0.03,
            'noise_filter_min_neighbors': 5,
        }],
        remappings=[
            ('rgb/image', '/my_go2/color/image_raw_sync'),
            ('depth/image', '/my_go2/depth/image_rect_raw_sync'),
            ('rgb/camera_info', '/my_go2/color/camera_info_sync'),
            ('cloud', '/rgbd_cloud'),
        ],
    )

    # 3) RTAB-Map SLAM 노드
    rtabmap_slam = Node(
        package='rtabmap_slam',
        executable='rtabmap',
        name='rtabmap',
        output='screen',
        respawn=True,
        respawn_delay=2.0,
        condition=UnlessCondition(use_lidar),
        parameters=[base_slam_parameters],
        remappings=base_remappings,
    )
    rtabmap_slam_lidar = Node(
        package='rtabmap_slam',
        executable='rtabmap',
        name='rtabmap',
        output='screen',
        respawn=True,
        respawn_delay=2.0,
        condition=IfCondition(use_lidar),
        parameters=[dict(
            base_slam_parameters,
            subscribe_scan_cloud=True,
            approx_sync_max_interval=0.5,
            topic_queue_size=50,
            sync_queue_size=100,
        )],
        remappings=base_remappings + [('scan_cloud', '/utlidar/cloud_sync')],
    )

    # 4) RTAB-Map 시각화 노드 (GUI)
    rtabmap_viz = Node(
        package='rtabmap_viz',
        executable='rtabmap_viz',
        name='rtabmap_viz',
        output='screen',
        condition=IfCondition(PythonExpression(["'", use_lidar, "' == 'false' and '", use_viz, "' == 'true'"])),
        parameters=[base_viz_parameters],
        remappings=base_remappings,
    )
    rtabmap_viz_lidar = Node(
        package='rtabmap_viz',
        executable='rtabmap_viz',
        name='rtabmap_viz',
        output='screen',
        condition=IfCondition(PythonExpression(["'", use_lidar, "' == 'true' and '", use_viz, "' == 'true'"])),
        parameters=[dict(
            base_viz_parameters,
            subscribe_scan_cloud=True,
            approx_sync_max_interval=0.5,
            topic_queue_size=50,
            sync_queue_size=100,
        )],
        remappings=base_remappings + [('scan_cloud', '/utlidar/cloud_sync')],
    )

    # 5) 런치 구성 반환
    return LaunchDescription([
        DeclareLaunchArgument('use_viz', default_value='false'),
        DeclareLaunchArgument('odom_eval_mode', default_value='false'),
        DeclareLaunchArgument('use_lidar', default_value='true'),
        SetEnvironmentVariable(name='ROS_LOG_DIR', value='/tmp/ros_logs'),
        LogInfo(msg="Go2 비주얼 SLAM 시작 (토픽 동기화 포함, 실제 로봇 모드)..."),
        topic_sync,
        rgbd_cloud,
        rtabmap_slam,
        rtabmap_slam_lidar,
        rtabmap_viz,
        rtabmap_viz_lidar
    ])
