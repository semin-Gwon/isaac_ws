# 환경 설정

## Quick Start · 처음 설치하는 순서

| 순서 | 작업 | 대상 |
| --- | --- | --- |
| 1 | [사전 준비·ROS·저장소](#prerequisites) | 공통 |
| 2 | [Conda·Isaac Sim·Isaac Lab](#isaac) | 디지털 트윈 |
| 3 | [Unitree 메시지 빌드](#messages) | 공통 · 사용 기능에 따라 빌드 선택 |
| 4 | [Go2 모델·PC 경로](#model) | 디지털 트윈 |
| 5 | [네트워크·센서 드라이버](#sensors) | 공통 |
| 6 | [수신 확인 → 실행](#verify) | 공통 |

- 처음 설치: 위에서 아래 순서 · 각 단계 성공 후 다음 단계
- SLAM만 사용: 2·4단계 생략 · 3단계의 **SLAM 전용 빌드** 선택
- 설치 완료: [디지털 트윈 Quick Start](../go2_real/digital_twin/README.md#quick-start) / [SLAM Quick Start](../go2_real/slam/README.md#quick-start)

<a id="prerequisites"></a>

## 1. 사전 준비·ROS·저장소

### Prerequisites

| 항목 | 조건 |
| --- | --- |
| PC | Ubuntu 22.04 x86_64 · Bash · 인터넷 |
| 디지털 트윈 GPU | RT Core 지원 NVIDIA GPU·드라이버·GUI · [Isaac Sim 5.1 요구사항](https://docs.isaacsim.omniverse.nvidia.com/5.1.0/installation/requirements.html) |
| 디지털 트윈 메모리·저장장치 | 공식 최소 RAM 32GB · SSD 50GB, 설치·빌드용 추가 여유 공간 |
| 로봇 | DDS로 `/lf/lowstate`·`/utlidar/robot_odom`을 발행하는 Go2 |
| 카메라 | RGB용 ROS Image / 뎁스·RGB-D SLAM용 RGB-D 카메라 |
| 통신 | PC↔Go2 유선 연결 · 같은 서브넷·ROS domain |

### ROS 2 Humble 설치

1. [ROS 공식 Ubuntu 설치 안내](https://docs.ros.org/en/humble/Installation/Ubuntu-Install-Debs.html): apt 저장소 등록 → `ros-humble-ros-base` 또는 `ros-humble-desktop` 설치
2. **새 시스템 터미널 · Conda 비활성 상태**에서 아래 실행

```bash
sudo apt update
sudo apt install -y git curl build-essential cmake python3-dev python3-pip \
  python3-colcon-common-extensions \
  ros-humble-rosidl-default-generators ros-humble-rosidl-generator-dds-idl \
  ros-humble-rmw-cyclonedds-cpp ros-humble-sensor-msgs \
  ros-humble-nav-msgs ros-humble-geometry-msgs ros-humble-tf2-msgs
source /opt/ros/humble/setup.bash
ros2 --help
git clone https://github.com/semin-Gwon/isaac_ws.git "$HOME/isaac_ws"
```

- 완료 기준: `ros2 --help` 출력 · `~/isaac_ws/src/unitree_go` 존재
- `unitree_go`·`unitree_api`: 저장소에 포함 · [원본 Unitree ROS 2](https://github.com/unitreerobotics/unitree_ros2) 추가 복제 불필요
- `unitree_api`: 로봇 제어용 메시지 · 현재 시각화 실행에는 빌드 불필요

### 기능별 추가 패키지

**RealSense 카메라를 이 PC에 연결하는 경우** · [공식 ROS 드라이버](https://github.com/realsenseai/realsense-ros)

```bash
sudo apt install -y ros-humble-realsense2-camera \
  ros-humble-realsense2-description ros-humble-librealsense2
```

- USB 권한·장치 인식 문제: [공식 udev 설정](https://github.com/realsenseai/librealsense/blob/master/doc/installation.md#install-librealsense2)의 `scripts/setup_udev_rules.sh` 실행 → USB 재연결

**SLAM을 사용하는 경우** · [RTAB-Map ROS](https://github.com/introlab/rtabmap_ros)

```bash
sudo apt install -y ros-humble-rtabmap-ros ros-humble-rviz2 \
  ros-humble-tf2-ros python3-numpy python3-opencv
```

<a id="isaac"></a>

## 2. Conda·Isaac Sim·Isaac Lab

### Conda · 미설치 시 1회

- [Miniforge](https://github.com/conda-forge/miniforge) 사용 · 설치 경로 `~/anaconda3`로 실행기 기본값과 통일
- 기존 Conda 사용 시: 설치 생략 · 아래 경로를 실제 설치 위치로 변경

```bash
curl -fL -o /tmp/Miniforge3.sh \
  https://github.com/conda-forge/miniforge/releases/latest/download/Miniforge3-Linux-x86_64.sh
bash /tmp/Miniforge3.sh -b -p "$HOME/anaconda3"
```

### 시뮬레이터·Python 모듈

**새 터미널 · 시스템 ROS 환경과 분리**

```bash
source "$HOME/anaconda3/etc/profile.d/conda.sh"
conda create -y -n isaaclab python=3.11
conda activate isaaclab
unset PYTHONPATH LD_LIBRARY_PATH AMENT_PREFIX_PATH COLCON_PREFIX_PATH CMAKE_PREFIX_PATH

python -m pip install --upgrade pip
python -m pip install "isaacsim[all,extscache]==5.1.0.0" --extra-index-url https://pypi.nvidia.com
python -m pip install torch==2.7.0 torchvision==0.22.0 --index-url https://download.pytorch.org/whl/cu128

git clone https://github.com/isaac-sim/IsaacLab.git "$HOME/IsaacLab"
git -C "$HOME/IsaacLab" checkout c627940ae17429b1d35aba667162fcf998fad17c
python -m pip install -e "$HOME/IsaacLab/source/isaaclab"
python -m pip install "numpy==1.26.4" "setuptools==80.10.2" \
  "empy==3.3.4" "catkin-pkg==1.1.0" "lark-parser==0.12.0"

python "$HOME/IsaacLab/scripts/tutorials/00_sim/create_empty.py"
```

- 완료 기준: 빈 Isaac Sim 뷰포트 → 종료 `Ctrl+C`
- 최초 실행: NVIDIA 사용 약관 확인·동의 / 확장·에셋 다운로드 대기
- 버전: 로컬 실행 확인 조합 고정 · Isaac Lab `0.48.5`의 Git 커밋 사용
- Isaac Sim 5.1: 공식 지원 종료 버전 · 이 안내는 기존 프로젝트 재현 기준
- 참고: [Isaac Lab 설치](https://isaac-sim.github.io/IsaacLab/v2.3.0/source/setup/installation/pip_installation.html) · [Isaac Sim ROS/Python 3.11](https://docs.isaacsim.omniverse.nvidia.com/5.1.0/installation/install_ros.html)

| 모듈 | 제공 위치 |
| --- | --- |
| `isaaclab.app.AppLauncher` | 위 Isaac Lab 소스 설치 |
| `carb`·`omni.*`·`pxr` | Isaac Sim 번들 · 개별 pip 설치 대상 아님 |
| `rclpy`·표준 ROS 메시지 | Sim 실행: 내장 Python 3.11 / SLAM: 시스템 Python 3.10 |
| `numpy` | 디지털 트윈: pip / SLAM: apt |
| `cv2` | SLAM의 `python3-opencv` |
| `empy`·`catkin-pkg`·`lark-parser` | Python 3.11 메시지 빌드 |

<a id="messages"></a>

## 3. Unitree 메시지 빌드

### 디지털 트윈 또는 두 기능 모두 · Python 3.11

**새 시스템 터미널 · Conda 비활성 상태**

```bash
source /opt/ros/humble/setup.bash
GO2_ENV="$HOME/anaconda3/envs/isaaclab"
cd "$HOME/isaac_ws"
/usr/bin/python3 -m colcon build --packages-select unitree_go \
  --cmake-args -DCMAKE_BUILD_TYPE=Release -DBUILD_TESTING=OFF \
  -DPython3_EXECUTABLE="$GO2_ENV/bin/python" \
  -DPYTHON_EXECUTABLE="$GO2_ENV/bin/python" \
  -DPYTHON_INCLUDE_DIR="$GO2_ENV/include/python3.11" \
  -DPYTHON_LIBRARY="$GO2_ENV/lib/libpython3.11.so" \
  -DCMAKE_C_COMPILER=/usr/bin/cc -DCMAKE_CXX_COMPILER=/usr/bin/c++

bash "$HOME/isaac_ws/go2_real/digital_twin/run.sh" check
```

- 완료 기준: `LowState`·표준 메시지·`CycloneDDS library: OK`
- 생성 위치: `install/unitree_go/lib/python3.11/site-packages`
- 시스템 ROS 사용: **빌드 단계만** · Sim 실행 환경은 `run.sh`가 재설정
- 실제 검증: 별도 임시 폴더에서 빌드 → Sim 내장 ROS로 타입 로딩·직렬화/역직렬화 통과

<details>
<summary>SLAM만 사용할 때 · Python 3.10 대체 빌드</summary>

**위 Python 3.11 빌드 대신 실행 · 새 워크스페이스 기준**

```bash
source /opt/ros/humble/setup.bash
cd "$HOME/isaac_ws"
/usr/bin/python3 -m colcon build --packages-select unitree_go \
  --cmake-args -DBUILD_TESTING=OFF \
  -DPython3_EXECUTABLE=/usr/bin/python3 -DPYTHON_EXECUTABLE=/usr/bin/python3
source "$HOME/isaac_ws/scripts/env_ros2.sh"
```

- 목적: 현재 SLAM 환경 스크립트가 요구하는 `install/local_setup.bash` 생성
- SLAM 노드 자체: 표준 메시지 사용
- 디지털 트윈 실행기: Python 3.11 빌드 필수 · 이 결과와 호환 불가
- 완료 기준: 빌드 성공 · `env_ros2.sh` 오류 없이 로딩

</details>

- 두 빌드 중 하나만 선택 · 기존 `build/`·`install/`에 Python 버전 혼합 금지
- 다른 버전으로 전환: 기존 결과 보관·분리 후 빈 빌드/설치 경로에서 재빌드

<a id="model"></a>

## 4. Go2 모델·PC 경로

### URDF·mesh 다운로드

현재 코드에서 사용한 [Go2 모델 저장소](https://github.com/Unitree-Go2-Robot/go2_description):

```bash
mkdir -p "$HOME/go2_ws/src"
git clone https://github.com/Unitree-Go2-Robot/go2_description.git \
  "$HOME/go2_ws/src/go2_description"
git -C "$HOME/go2_ws/src/go2_description" checkout 8bd6717ff0c7b5ca388c0e10e426dd9ad873ceaf
test -f "$HOME/go2_ws/src/go2_description/urdf/go2_description.urdf"
```

- 저장소 전체 유지: URDF + `dae/` 등 mesh 파일 필요
- 모델만 사용: 별도 Go2 SDK 전체 설치 불필요

### 개발 PC의 고정 경로 변경 · 최초 1회

아래 명령: 두 시각화 파일의 `/home/jnu` 경로를 **현재 사용자 경로**로 변경.

```bash
python3 - <<'PY'
from pathlib import Path
user_root = Path.home()
for name in ("go2_visualize.py", "go2_digital_twin.py"):
    path = user_root / "isaac_ws/go2_real/digital_twin" / name
    text = path.read_text()
    text = text.replace("/home/jnu/anaconda3", str(user_root / "anaconda3"))
    text = text.replace("/home/jnu/go2_ws", str(user_root / "go2_ws"))
    path.write_text(text)
    print("Updated:", path)
PY
```

- 사용자 지정 Conda 위치: `GO2_CONDA_ROOT` 설정 + 위 치환 경로도 변경
- 사용자 지정 모델 위치: 두 파일의 `urdf_path` 변경
- PLY 도구만 사용 시: `tools/go2_import_ply.py`의 `ply_file_path` 별도 변경

<a id="sensors"></a>

## 5. 네트워크·센서 드라이버

### Go2 · DDS 연결

1. PC와 Go2 유선 연결
2. Ubuntu 네트워크 설정: 로봇과 같은 서브넷의 미사용 고정 IPv4
3. `ip -br address`로 유선 인터페이스 확인
4. [cyclonedds.xml](../config/cyclonedds.xml)의 `eno1`을 실제 인터페이스 이름으로 변경

| 설정 | 기준 |
| --- | --- |
| PC IPv4 예시 | `192.168.123.99/24` · 로봇 네트워크가 `192.168.123.0/24`일 때 |
| RMW / Domain | `rmw_cyclonedds_cpp` / `0` |
| DDS 설정 적용 | 프로젝트의 환경 스크립트에서 자동 적용 |
| 로봇 입력 | `/lf/lowstate` · `/utlidar/robot_odom` · `/utlidar/cloud_base` 등 |

- 연결 기준: [Unitree 공식 네트워크 안내](https://github.com/unitreerobotics/unitree_ros2#1-network-configuration)
- 로봇의 상태·LiDAR 발행 기능 활성화 필요 · 이 저장소는 드라이버를 시작하지 않음
- WebRTC 방식: [별도 Go2 ROS 2 SDK](https://github.com/abizovnuralem/go2_ros2_sdk) 참고 · 토픽·메시지 타입 호환 작업 필요, 이 Quick Start의 직접 DDS 구성과 별도

### RGB-D 카메라 · RealSense 예시

**카메라가 연결된 PC의 별도 시스템 ROS 터미널**:

```bash
source "$HOME/isaac_ws/scripts/env_ros2.sh"
ros2 launch realsense2_camera rs_launch.py \
  camera_namespace:=/ camera_name:=camera \
  enable_color:=true enable_depth:=true align_depth.enable:=true
```

- 드라이버: [RealSense ROS](https://github.com/realsenseai/realsense-ros)
- `camera_namespace:=/`: 프로젝트의 `/camera/...` 토픽 이름과 일치
- 이미 카메라 토픽 발행 중: 드라이버 중복 실행 생략
- 다른 카메라: 해당 제조사 ROS 드라이버 + `Image`·`CameraInfo` 발행 필요
- 원격 PC에서 발행: 해당 PC에도 동일한 DDS domain·네트워크 설정 적용

<a id="verify"></a>

## 6. 수신 확인 → 실행

### 토픽 확인 · 별도 터미널

```bash
source "$HOME/isaac_ws/scripts/env_ros2.sh"
ros2 topic list -t
ros2 topic info /lf/lowstate
```

| 기능 | 확인할 입력 |
| --- | --- |
| 관절·자세 | `/lf/lowstate`의 `unitree_go/msg/LowState` · `/utlidar/robot_odom` |
| RGB 화면 | `/camera/color/image_raw` |
| 뎁스·LiDAR 진단 | `/camera/aligned_depth_to_color/image_raw` · `/utlidar/cloud_base` |
| RGB-D SLAM | RGB + `/camera/depth/image_rect_raw` + `/camera/color/camera_info` + Odom·IMU |
| SLAM의 LiDAR 모드 | 위 입력 + PointCloud2 · 시간·TF 매칭 |

- 성공 기준: 필요한 토픽·타입 확인 + Publisher count 1 이상
- `run.sh check`: 라이브러리 검사 / `run.sh sensors --seconds 10`: 실제 뎁스·LiDAR 수신 검사
- `sensors` 사용: 디지털 트윈용 Python 3.11 설치 필요
- 실행: [디지털 트윈](../go2_real/digital_twin/README.md#quick-start) / [SLAM](../go2_real/slam/README.md#quick-start)
- 카메라 모드: 별도 관절 변환기 불필요 / `sim` 모드: `bridge` 필수

## 설치 문제 확인

| 증상 | 확인 |
| --- | --- |
| `/opt/ros/humble/setup.bash` 없음 | 1단계 ROS 설치 |
| `install/local_setup.bash` 없음 | 3단계 메시지 빌드 |
| `unitree_go`·`_rclpy` 로딩 오류 | Python 3.10/3.11 혼용·빌드 결과·환경 경로 |
| `em`·`Lark`·`catkin_pkg` 오류 | 2단계 빌드용 pip 모듈 |
| URDF·mesh 없음 | 4단계 모델 다운로드·고정 경로 치환 |
| 카메라 미수신 | 드라이버·namespace·실제 RGB 토픽 |
| 로봇 미수신 | NIC·IPv4·domain·로봇 발행 상태 |
| SLAM 무출력 | 원본 Depth·CameraInfo·IMU·TF·센서 시각 확인 |

- 검증 범위: 기존 PC의 카메라 실행 + Python 3.11·3.10 메시지 신규 빌드·타입 검사
- 미검증: 깨끗한 OS에서 전체 재설치 · 모든 카메라/Go2 펌웨어 · SLAM 지도 품질
- 생성 파일: [Git 제외 규칙](../.gitignore) / [SLAM 지도 저장](../go2_real/slam/README.md#지도-저장)

[메인](../README.md) · [디지털 트윈](../go2_real/digital_twin/README.md) · [SLAM](../go2_real/slam/README.md)
