#!/usr/bin/env bash
# Configure the environment and replace this shell with the selected process.

usage() {
    cat <<'USAGE'
사용법: bash go2_real/digital_twin/run.sh <bridge|sim|camera|sensors|check> [추가 인자]

  bridge  LowState → JointState 변환기 실행 (터미널 1)
  sim     Isaac Sim 관절·자세 디지털 트윈 실행 (터미널 2)
  camera  관절·RGB 시각화 및 뎁스·LiDAR 구독 실행
  sensors 뎁스·LiDAR 실제 수신 검사 (기본 10초, Isaac Sim 실행 없음)
  check   Python·메시지·CycloneDDS 라이브러리 검사 (ROS 노드 실행 없음)

예: bash go2_real/digital_twin/run.sh sim --headless
환경 변수: GO2_CONDA_ROOT (기본값: $HOME/anaconda3)
USAGE
}

case "${1:-}" in
    -h|--help) usage; exit 0 ;;
    bridge) target=ros2_bridge_server.py ;;
    sim) target=go2_digital_twin.py ;;
    camera) target=go2_visualize.py ;;
    sensors) target=sensor_subscriptions.py ;;
    check) target=check ;;
    *) usage >&2; exit 2 ;;
esac
shift
if [[ "$target" == check && "$#" -ne 0 ]]; then
    echo "check에는 추가 인자를 사용할 수 없습니다." >&2
    exit 2
fi

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)" || exit 1
source "$script_dir/env_go2_visualize.sh" || exit 1
cd -- "$GO2_WS" || exit 1

if [[ "$target" == check ]]; then
    exec "$GO2_PYTHON" - <<'PY'
import ctypes
import os
import sys

import rclpy
from rclpy.type_support import check_for_type_support
from unitree_go.msg import LowState
from sensor_msgs.msg import Image, JointState, PointCloud2
from nav_msgs.msg import Odometry
from tf2_msgs.msg import TFMessage

print(f"Python: {sys.version.split()[0]} ({sys.executable})")
for message_type in (LowState, JointState, Odometry, TFMessage, Image, PointCloud2):
    check_for_type_support(message_type)
    print(f"{message_type.__name__}: OK")
ctypes.CDLL(os.path.join(os.environ["ISAAC_ROS_BRIDGE"], "lib", "librmw_cyclonedds_cpp.so"))
print("CycloneDDS library: OK")
print(f"DDS config: {os.environ['CYCLONEDDS_URI']}")
print("환경 검사 완료. 실제 로봇 연결이나 Isaac Sim 실행 검사는 포함하지 않습니다.")
PY
fi

exec "$GO2_PYTHON" "$script_dir/$target" "$@"
