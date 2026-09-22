#!/usr/bin/env bash
# System ROS environment for SLAM and ROS CLI inspection.
# Usage: source /path/to/isaac_ws/scripts/env_ros2.sh
if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
    echo "환경만 설정하려면 source를 사용하세요: source \"$0\"" >&2
    exit 2
fi

_go2_setup_system_ros() {
    local workspace_root
    workspace_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)" || return 1
    source /opt/ros/humble/setup.bash || return 1
    source "$workspace_root/install/local_setup.bash" || return 1

    export RMW_IMPLEMENTATION=rmw_cyclonedds_cpp
    export ROS_DOMAIN_ID=0
    export ROS_LOCALHOST_ONLY=0
    export CYCLONEDDS_URI="file://$workspace_root/config/cyclonedds.xml"
    export ROS_LOG_DIR=/tmp/ros_logs
    mkdir -p "$ROS_LOG_DIR" || return 1
}

if _go2_setup_system_ros; then
    unset -f _go2_setup_system_ros
else
    unset -f _go2_setup_system_ros
    return 1
fi
