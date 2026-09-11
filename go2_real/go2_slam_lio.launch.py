import os
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, LogInfo, SetEnvironmentVariable
from launch.conditions import IfCondition
from launch.substitutions import LaunchConfiguration, PythonExpression
from launch_ros.actions import Node

_THIS_DIR = os.path.dirname(os.path.realpath(__file__))
_MAPS_DIR = os.path.join(_THIS_DIR, 'maps')
_RTABMAP_DB_PATH = os.path.join(_MAPS_DIR, 'rtabmap_real_lio.db')

def generate_launch_description():
    os.makedirs(_MAPS_DIR, exist_ok=True)

    use_sim_time = LaunchConfiguration('use_sim_time', default='false')
    use_viz = LaunchConfiguration('use_viz', default='false')
    use_lidar = LaunchConfiguration('use_lidar', default='true')

    base_slam_parameters = {
        'map_frame_id': 'map',
        'odom_frame_id': 'odom',
        'frame_id': 'base_link',
        'publish_tf': True,
        'subscribe_depth': False,
        'subscribe_rgb': False,
        'subscribe_imu': False,
        'subscribe_scan': False,
        'approx_sync': True,
        'approx_sync_max_interval': 1.0,
        'use_sim_time': use_sim_time,
        'queue_size': 50,
        'topic_queue_size': 10,
        'sync_queue_size': 20,
        'wait_for_transform': 2.0,
        'database_path': _RTABMAP_DB_PATH,
        'Reg/Strategy': '1',
        'Reg/Force3DoF': 'true',
        'Rtabmap/DetectionRate': '1.0',
        'RGBD/OptimizeMaxError': '6.0',
        'RGBD/ProximityBySpace': 'true',
        'RGBD/ProximityPathMaxNeighbors': '1',
        'RGBD/ProximityMaxGraphDepth': '0',
        'Mem/RehearsalSimilarity': '0.35',
        'Mem/NotLinkedNodesKept': 'false',
        'Icp/VoxelSize': '0.10',
        'Icp/MaxCorrespondenceDistance': '0.20',
        'Icp/Iterations': '12',
        'Icp/PointToPlane': 'true',
        'Icp/OutlierRatio': '0.7',
        'Optimizer/GravitySigma': '0.3',
        'Cloud/VoxelSize': '0.06',
        'Grid/CellSize': '0.10',
        'Grid/RangeMax': '8.0',
    }

    base_viz_parameters = {
        'map_frame_id': 'map',
        'odom_frame_id': 'odom',
        'frame_id': 'base_link',
        'publish_tf': True,
        'subscribe_depth': False,
        'subscribe_rgb': False,
        'subscribe_imu': False,
        'subscribe_scan': False,
        'approx_sync': True,
        'approx_sync_max_interval': 1.0,
        'use_sim_time': use_sim_time,
        'queue_size': 50,
        'topic_queue_size': 10,
        'sync_queue_size': 20,
        'wait_for_transform': 2.0,
        'Reg/Strategy': '1',
        'Reg/Force3DoF': 'true',
        'Rtabmap/DetectionRate': '1.0',
        'RGBD/ProximityBySpace': 'true',
        'RGBD/ProximityPathMaxNeighbors': '1',
        'RGBD/ProximityMaxGraphDepth': '0',
        'Icp/VoxelSize': '0.10',
        'Icp/MaxCorrespondenceDistance': '0.20',
        'Icp/Iterations': '12',
        'Icp/PointToPlane': 'true',
        'Icp/OutlierRatio': '0.7',
        'Optimizer/GravitySigma': '0.3',
        'Cloud/VoxelSize': '0.06',
        'Grid/CellSize': '0.10',
        'Grid/RangeMax': '8.0',
    }

    base_remappings = [
        ('imu', '/utlidar/imu_synced'),
        ('odom', '/utlidar/robot_odom_synced'),
    ]

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
            approx_sync_max_interval=1.0,
            topic_queue_size=20,
            sync_queue_size=40,
        )],
        remappings=base_remappings + [('scan_cloud', '/utlidar/cloud_deskewed_synced')],
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
            approx_sync_max_interval=1.0,
            topic_queue_size=20,
            sync_queue_size=40,
        )],
        remappings=base_remappings + [('scan_cloud', '/utlidar/cloud_deskewed_synced')],
    )

    return LaunchDescription([
        DeclareLaunchArgument('use_sim_time', default_value='false'),
        DeclareLaunchArgument('use_viz', default_value='false'),
        DeclareLaunchArgument('use_lidar', default_value='true'),
        SetEnvironmentVariable(name='ROS_LOG_DIR', value='/tmp/ros_logs'),
        LogInfo(msg='Go2 SLAM (RTAB-Map only, expects *_synced topics) starting...'),
        rtabmap_slam_lidar,
        rtabmap_viz_lidar,
    ])
