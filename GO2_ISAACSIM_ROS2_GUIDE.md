# IsaacSim + 실제 Go2 로봇 ROS 2 연동 상세 가이드

이 문서는 실제 Go2 로봇의 데이터를 Isaac Sim 환경으로 가져와 시각화하는 전체 시스템의 구조와 원리를 상세하게 설명합니다. 초보자도 이해하기 쉽도록 구체적인 작동 방식과 설정 이유를 포함했습니다.

## 1. 시스템 아키텍처 (System Architecture)

아래 다이어그램은 두 개의 독립된 터미널 환경에서 실행되는 프로그램들이 데이터를 주고받는 전체 구조를 보여줍니다.

```text
+-------------------------------------------------------------+
|              [Terminal 1] RoboStack Environment             |
|                                                             |
|   +-----------------------------------------------------+   |
|   |  ros2_bridge_server.py                              |   |
|   |  [Python 3.11 + rclpy]                              |   |
|   |                                                     |   |
|   |  (Input)  <-- /lf/lowstate (Unitree Custom Msg)     |   |
|   |  (Output) --> /joint_states (Standard ROS 2 Msg)    |   |
|   +--------------------------+--------------------------+   |
|                              |                              |
+------------------------------|------------------------------+
                               |
                               | DDS (Data Distribution Service)
                               | ethernet cable
                               v
+------------------------------|------------------------------+
|               [Terminal 2] Isaac Sim Environment            |
|                                                             |
|   +-----------------------------------------------------+   |
|   |  go2_delay.py (Executed via python -m isaacsim)     |   |
|   |  [Python 3.10/3.11 + Isaac Sim Libs]                |   |
|   |  * No direct rclpy usage to avoid conflicts *       |   |
|   |                                                     |   |
|   |  [OmniGraph Node Flow]                              |   |
|   |  JointState Sub -> Articulation Controller          |   |
|   |  Image Sub -> Dynamic Texture -> UI Window          |   |
|   +-----------------------------------------------------+   |
|                                                             |
+-------------------------------------------------------------+
```

## 1.1 아키텍처 및 데이터 흐름 상세 설명

이 시스템은 **"호환성 문제 해결"** 과 **"실시간 성능 확보"** 를 위해 두 단계로 설계되었습니다.

### 1) 왜 두 개의 터미널(환경)로 나누어 실행하나요?
가장 큰 이유는 **"라이브러리 충돌 방지"** 와 **"메시지 표준화"** 입니다. 아래 다이어그램은 두 환경이 어떻게 분리되어 작동하는지를 시각적으로 보여줍니다.

```text
      [ Real Go2 Robot ]
      (Physical Hardware)
             |
             | /lf/lowstate (Ethernet/UDP)
             v
+-------------------------------------------------+
| [ Terminal 1: RoboStack Environment ]           |
|                                                 |
|    [ ros2_bridge_server.py ]                    |
|             |                                   |
|    (Convert to ROS2 Standard)                   |
|             |                                   |
|             v                                   |
|    [ /joint_states ]                            |
+-------------+-----------------------------------+
              |
              | DDS (Topic: /joint_states)
              v
+-------------------------------------------------+
| [ Terminal 2: Isaac Sim Environment ]           |
|                                                 |
|    [ OmniGraph: ROS2SubscribeJointState ]       |
|             |                                   |
|             v                                   |
|    [ Articulation Controller ]                  |
|             |                                   |
|             v                                   |
|    [ Virtual Go2 Robot ]                        |
+-------------------------------------------------+
```

*   **메시지 표준화 (Message Standardization)**:
    *   **Go2 로봇**은 `unitree_go/msg/LowState`라는 **Unitree 전용 메시지 형식**을 사용합니다. 이 안에는 각 모터의 각도(`q`), 속도(`dq`), 토크(`tau`) 정보가 포함되어 있습니다.
    *   **Isaac Sim**의 기본 ROS 2 브리지 기능은 `sensor_msgs/msg/JointState`라는 **ROS 2 표준 메시지**를 수신하도록 최적화되어 있습니다.
    *   따라서, 터미널 1의 `ros2_bridge_server.py`가 중간에서 **전용 메시지를 표준 메시지로 변환**해주는 역할을 수행합니다.

*   **라이브러리 충돌 방지 (Avoiding Dependency Conflicts)**:
    *   `ros2_bridge_server.py`는 `unitree_go` 메시지를 해석하기 위해 특정 라이브러리가 필요합니다.
    *   반면, **Isaac Sim**은 고성능 시뮬레이션을 위해 복잡한 자체 Python 환경을 가지고 있습니다. 여기에 외부 ROS 2 라이브러리(`rclpy` 등)를 강제로 섞으면 **시스템 충돌(Segmentation Fault 등)** 이 자주 발생합니다.
    *   가장 안정적인 방법은 **"데이터 변환기(터미널 1)"** 와 **"시뮬레이터(터미널 2)"** 를 완전히 분리된 환경(Conda env)에서 실행하고, 오직 ROS 2 통신 프로토콜(DDS)로만 데이터를 주고받는 것입니다.

### 2) 데이터 처리 및 흐름 (Detailed Data Flow)

데이터가 로봇에서 출발하여 시뮬레이터 화면에 나타나기까지의 과정을 시퀀스 다이어그램으로 표현했습니다.

```text
(Real Robot)        (Terminal 1: Bridge)       (Terminal 2: Isaac Sim)
    |                       |                          |
    |  --- [Ethernet] ----> |                          |
    |                       |                          |
    |                       |                          |
    |==== Loop: High Frequency (500Hz) ================================
    |                       |                          |
    |  /lf/lowstate         |                          |
    |---------------------->|                          |
    |                       | [Convert]                |
    |                       | LowState -> JointState   |
    |                       |                          |
    |                       |      /joint_states       |
    |                       |------------------------->|
    |                       |          (DDS)           |  [OmniGraph]
    |                       |                          |  Sub -> Control
    |                       |                          |
    |==================================================================
    |                       |                          |
    |==== Loop: Camera Stream (30Hz) ==================================
    |                       |                          |
    |          /my_go2/color/image_raw                 |
    |------------------------------------------------->|
    |                  (Direct DDS)                    |  [OmniGraph]
    |                       |                          |  Image -> Texture -> UI
    |                       |                          |
    |==================================================================
```

데이터가 로봇에서 출발하여 시뮬레이터 화면에 나타나기까지의 텍스트 설명입니다.

1.  **데이터 수신 (Real Go2 Robot → Terminal 1)**:
    *   실제 로봇이 자신의 관절 상태를 `/lf/lowstate` 토픽으로 전송합니다 (유선 랜 연결).
    *   **`ros2_bridge_server.py`** 에서 이 데이터를 수신합니다.

2.  **데이터 변환 (Processing inside Bridge)**:
    *   **메시지 매핑**: 받은 `LowState` 데이터의 `motor_state` 배열에서 각 다리 관절(FR_hip, FR_thigh, FR_calf 등)의 각도, 속도, 토크 값을 추출합니다.
    *   **표준화**: 추출된 값을 `JointState` 표준 메시지 포맷의 `position`, `velocity`, `effort` 필드에 채워 넣습니다.
    *   **타임스탬프 동기화**: 현재 시각을 메시지 헤더에 기록하여 시뮬레이터가 데이터 발생 시점을 알 수 있게 합니다.

3.  **데이터 전송 (Terminal 1 → Terminal 2 via DDS)**:
    *   변환된 `/joint_states` 메시지를 ROS 2 네트워크(DDS)로 발행(Publish)합니다.

4.  **시뮬레이션 적용 (Terminal 2: Isaac Sim OmniGraph)**:
    *   **OmniGraph (옴니그래프)**: Isaac Sim 내부의 **노드 기반 비주얼 프로그래밍 시스템**입니다. 파이썬 코드가 매 프레임 실행되는 것보다 훨씬 빠르고 효율적(C++ 레벨 처리)입니다.
    *   **`ROS2SubscribeJointState` 노드**: DDS를 통해 `/joint_states` 데이터를 직접 수신합니다. 파이썬 `rclpy`를 거치지 않습니다.
    *   **`IsaacArticulationController` 노드**: 수신한 관절 각도 데이터를 가상 로봇 모델의 관절 모터에 즉시 적용하여 움직임을 만들어냅니다.

5.  **카메라 시각화 (Camera Visualization)**:
    *   **`ROS2SubscribeImage` 노드**: 로봇 카메라 데이터(`/my_go2/color/image_raw` 등)를 수신합니다.
    *   **`CreateTextureFromImage` 노드**: 받은 이미지 데이터를 실시간 텍스처(Dynamic Texture)로 변환합니다.
    *   이 텍스처는 가상 세계 속 스크린(`VirtualScreen`)과 별도 UI 창(`UI Window`) 양쪽에 동시에 표시됩니다.

---

## 2. 환경 설정 가이드 (Setup Guide)

### [Terminal 1] 데이터 변환용 환경 (RoboStack Bridge Env)
`ros2_bridge_server.py` 실행을 위한 환경입니다. `rclpy`와 `unitree_go` 패키지가 필요합니다.

```bash
# 1. 기존 환경 제거 (충돌 방지 및 클린 설치)
conda remove -n robostack_bridge --all -y

# 2. Python 3.11 환경 생성 (RoboStack 호환성 고려)
conda create -n robostack_bridge python=3.11 -y
conda activate robostack_bridge

# 3. ROS 2 Humble 설치 (RoboStack 채널 사용)
# - ros-humble-desktop: ROS 2 핵심 패키지 모음
conda install -c conda-forge -c robostack-humble ros-humble-desktop colcon-common-extensions -y

# 4. Unitree Go ROS 2 패키지 소스 빌드 (매우 중요!)
# - 다른 환경(isaaclab)에서 빌드된 캐시와 충돌하지 않도록 새로운 폴더에 빌드합니다.
mkdir -p ~/go2_bridge_ws/src
cd ~/go2_bridge_ws/src
git clone https://github.com/unitreerobotics/unitree_ros2.git
cd ~/go2_bridge_ws
colcon build --packages-select unitree_go
```

### [Terminal 2] 시뮬레이션 환경 (Isaac Sim Env)
`go2_delay.py` 실행을 위한 환경입니다. Isaac Sim이 설치된 Conda 환경을 사용합니다. **주의: 여기에는 `ros-humble` 패키지가 설치되어 있으면 안 됩니다.**

```bash
# 1. 환경 활성화
conda activate isaaclab

# 2. 충돌 패키지 확인 및 제거
# Isaac Sim 내부 라이브러리와 충돌을 일으킬 수 있는 외부 ROS 패키지를 제거합니다.
conda remove "ros-humble-*" -y
# ("Package not found" 메시지가 나오면 이미 깨끗한 상태이므로 정상입니다)
```

---

## 3. 실행 가이드 (Execution Guide)

### 1단계: 데이터 변환 서버 실행 (Terminal 1)
먼저 로봇의 데이터를 받아서 표준 포맷으로 바꿔주는 중계 서버를 켭니다.

```bash
conda activate robostack_bridge

# 빌드한 단위(Unitree) 메시지를 환경에 추가 (매우 중요!)
source ~/go2_bridge_ws/install/setup.bash

# 브리지 서버 실행
# 이 스크립트는 /lf/lowstate를 구독하고 /joint_states를 발행하기 시작합니다.
python /home/jnu/isaac_ws/go2_real/ros2_bridge_server.py
```
*   **성공 확인:** "Go2 Bridge Server Started..." 메시지가 출력되면 정상입니다.

### 2단계: Isaac Sim 시각화 실행 (Terminal 2)
변환된 데이터를 받아서 화면에 보여주는 시뮬레이터를 켭니다.

```bash
conda activate isaaclab

# [Network 설정]
# 로컬에서만 통신하도록 ID를 0으로 맞춥니다. (필요 시 변경 가능)
export ROS_DOMAIN_ID=0
# FastDDS 설정 파일 경로 지정 (필요한 경우)
export CYCLONEDDS_URI=file:///home/jnu/isaac_ws/cyclonedds.xml

# [실행]
# 주의: 'python'이 아니라 'python -m isaacsim'을 사용해야 Isaac Sim 모듈 경로가 올바르게 잡힙니다.
python -m isaacsim /home/jnu/isaac_ws/go2_real/go2_delay.py
```

---

## 4. 자주 묻는 질문 및 문제 해결 (Troubleshooting)

### Q1: `ModuleNotFoundError: No module named 'omni'` 오류가 발생해요.
*   **원인:** 일반 `python` 명령어로 실행하면 Isaac Sim 내부의 방대한 라이브러리 경로를 인식하지 못합니다.
*   **해결:** 반드시 **`python -m isaacsim 스크립트경로`** 형식으로 실행하세요. 이 래퍼(wrapper) 명령어가 필요한 모든 환경 변수를 자동으로 설정해 줍니다.

### Q2: 브리지 실행 시 `ModuleNotFoundError: No module named 'unitree_go'` 오류가 발생해요.
*   **원인:** `robostack_bridge` 환경에 Unitree 메시지 패키지가 설치되지 않았습니다.
*   **해결:** `pip install unitree-go` 명령어로 패키지를 설치해주세요.

### Q3: 로봇은 움직이는데 화면의 로봇은 가만히 있어요.
다음 체크리스트를 순서대로 확인해보세요:

1.  **DDS 도메인 ID 일치 확인:** 두 터미널 모두 `export ROS_DOMAIN_ID=0`이 입력되었나요? 서로 다른 ID를 쓰면 대화할 수 없습니다.
2.  **데이터 발행 확인:** Terminal 1에서 `ros2 topic hz /joint_states`를 입력했을 때 데이터가 들어오고 있나요? (Hz 값이 0이면 로봇 연결 문제)
3.  **방화벽 및 네트워크:** 로컬 통신(Loopback) 멀티캐스트가 차단되어 있을 수 있습니다. `sudo ufw allow in proto udp to 224.0.0.0/4` 명령어로 멀티캐스트를 허용해보세요.
4.  **토픽 이름 확인:** `go2_delay.py` 코드 내 `JointState` 구독 토픽명이 `/joint_states`로 정확히 설정되어 있는지 확인하세요.

### Q4: 카메라 화면이 안 나오거나 검은색으로 나와요.
*   **원인:** 실제 로봇의 카메라 스트림이 켜져 있지 않거나, Isaac Sim의 OmniGraph 노드가 데이터를 받지 못하는 상태입니다.
*   **해결:**
    *   로봇 측에서 카메라 노드가 실행 중인지 확인하세요.
    *   `go2_delay.py`의 `setup_camera_graph` 함수에서 구독하는 토픽명(`/my_go2/color/image_raw`)이 실제 발행되는 토픽명과 일치하는지 `ros2 topic list`로 확인하세요.
