#!/usr/bin/env bash

# Common environment for the bridge and Isaac Sim (Python 3.11).
# Source this file for an interactive shell, or use run.sh to launch a process.
if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
    echo "환경만 설정하려면 source를 사용하세요: source \"$0\"" >&2
    exit 2
fi

_go2_setup_twin_env() {
    local workspace_root conda_root unitree_python
    workspace_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)" || return 1
    conda_root="${GO2_CONDA_ROOT:-$HOME/anaconda3}"

    if [[ ! -r "$conda_root/etc/profile.d/conda.sh" ]]; then
        echo "Conda 설정 파일을 찾을 수 없습니다: $conda_root/etc/profile.d/conda.sh" >&2
        return 1
    fi
    if [[ ! -x "$conda_root/envs/isaaclab/bin/python" ]]; then
        echo "isaaclab 환경의 Python을 찾을 수 없습니다: $conda_root/envs/isaaclab/bin/python" >&2
        return 1
    fi

    # Replace inherited system ROS paths instead of appending incompatible libs.
    unset PYTHONHOME PYTHONPATH LD_LIBRARY_PATH
    unset AMENT_PREFIX_PATH COLCON_PREFIX_PATH CMAKE_PREFIX_PATH
    source "$conda_root/etc/profile.d/conda.sh" || return 1
    conda activate "$conda_root/envs/isaaclab" || return 1

    export GO2_WS="$workspace_root"
    export GO2_PYTHON="$CONDA_PREFIX/bin/python"
    "$GO2_PYTHON" -c 'import sys; sys.exit(0 if sys.version_info[:2] == (3, 11) else "디지털 트윈 실행기는 Python 3.11이 필요합니다.")' || return 1
    export ISAAC_ROS_BRIDGE="$CONDA_PREFIX/lib/python3.11/site-packages/isaacsim/exts/isaacsim.ros2.bridge/humble"
    unitree_python="$GO2_WS/install/unitree_go/lib/python3.11/site-packages"

    if [[ ! -d "$ISAAC_ROS_BRIDGE/rclpy" || ! -d "$ISAAC_ROS_BRIDGE/lib" ]]; then
        echo "Isaac Sim의 Python 3.11용 ROS 2 라이브러리를 찾을 수 없습니다: $ISAAC_ROS_BRIDGE" >&2
        return 1
    fi
    if [[ ! -d "$unitree_python/unitree_go" ]]; then
        echo "Python 3.11용 unitree_go 빌드가 필요합니다: $unitree_python" >&2
        return 1
    fi
    if [[ ! -r "$GO2_WS/config/cyclonedds.xml" ]]; then
        echo "DDS 설정 파일을 찾을 수 없습니다: $GO2_WS/config/cyclonedds.xml" >&2
        return 1
    fi

    export PYTHONPATH="$ISAAC_ROS_BRIDGE/rclpy:$unitree_python"
    export LD_LIBRARY_PATH="$ISAAC_ROS_BRIDGE/lib:$GO2_WS/install/unitree_go/lib"
    export ROS_DISTRO=humble
    export ROS_VERSION=2
    export RMW_IMPLEMENTATION=rmw_cyclonedds_cpp
    export ROS_DOMAIN_ID=0  # Match the Go2 DDS domain.
    export ROS_LOCALHOST_ONLY=0
    export CYCLONEDDS_URI="file://$GO2_WS/config/cyclonedds.xml"
    export ROS_LOG_DIR=/tmp/ros_logs
    mkdir -p "$ROS_LOG_DIR" || return 1
}

if _go2_setup_twin_env; then
    unset -f _go2_setup_twin_env
else
    unset -f _go2_setup_twin_env
    return 1
fi
