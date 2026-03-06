# Go2 RTAB-Map 통합 가이드 (isaac_ws)

이 문서는 현재 프로젝트(`/home/jnu/isaac_ws`)에서 실제로 작업한 내용만 기준으로 정리한 실행/운영 가이드입니다.

## 1) 목표와 최종 구성

목표:
- Go2 원본 토픽(odom/rgb/depth)을 받아 동기화 토픽으로 재발행
- RTAB-Map으로 SLAM 수행
- RViz로 맵/포인트클라우드/카메라 시각화

구성:
```text
[Go2 / 센서 퍼블리셔]
    |- /utlidar/robot_odom
    |- /my_go2/color/image_raw/compressed (또는 raw)
    |- /my_go2/depth/image_rect_raw (및 compressed)
            |
            v
[go2_topic_sync.py]
    |- /utlidar/robot_odom_sync
    |- /my_go2/color/image_raw_sync
    |- /my_go2/depth/image_rect_raw_sync
    |- /my_go2/color/camera_info_sync
    |- TF: odom->base_link, base_link->my_go2_color_optical_frame
            |
            v
[rtabmap_slam/rtabmap]
    |- /map, /mapData, /mapGraph, /cloud_map ...
            |
            v
[RViz]
    |- /map, /cloud_map, *_sync 이미지 토픽 표시
```

## 2) 변경된 핵심 파일

- `/home/jnu/isaac_ws/go2_real/go2_visualize.py`
  - `rclpy` 경로 주입(isaaclab 환경 대응)
  - QoS 조정(BEST_EFFORT)
  - 진단 로그 추가
  - `fix_base=False`로 변경

- `/home/jnu/isaac_ws/go2_real/go2_topic_sync.py`
  - 다중 토픽 구독(odom/rgb/depth)
  - 환경 변수 진단 로그 출력
  - depth `passthrough` 제거 및 `16UC1/32FC1` 정규화
  - compressed depth가 `uint8`일 때 스킵(비정량 depth 보호)
  - 카메라 TF 동적 재전송 타이머 추가

- `/home/jnu/isaac_ws/go2_real/go2_slam.launch.py`
  - `topic_sync` 자동 실행
  - `use_viz` 인자 기반 `rtabmap_viz` 조건부 실행
  - 동기화 버퍼 파라미터 확장
  - DetectionRate 조정

- `/home/jnu/isaac_ws/go2_sim.rviz`
  - 이미지 기본 토픽을 `_sync`로 변경
    - `/my_go2/color/image_raw_sync`
    - `/my_go2/depth/image_rect_raw_sync`

- `/home/jnu/isaac_ws/env_go2_slam.sh`
  - 공통 환경 설정 스크립트 추가 (`local_setup.bash` 기반)

## 3) 필수 환경 변수

모든 터미널에서 동일해야 통신이 안정적입니다.

```bash
source /opt/ros/humble/setup.bash
source /home/jnu/isaac_ws/install/local_setup.bash

export RMW_IMPLEMENTATION=rmw_cyclonedds_cpp
export ROS_DOMAIN_ID=0
export ROS_LOCALHOST_ONLY=0
export CYCLONEDDS_URI=file:///home/jnu/cyclonedds.xml
export ROS_LOG_DIR=/tmp/ros_logs
mkdir -p /tmp/ros_logs
```

권장 사용(새 터미널마다 1줄):
```bash
source /home/jnu/isaac_ws/env_go2_slam.sh
```

## 4) CycloneDDS 설정

현재 핵심 포인트:
- 로봇 peer + localhost peer 모두 등록
- `Addlocalhost` 속성 오타/비지원으로 인한 파싱 오류를 피함

예시:
```xml
<Discovery>
  <Peers>
    <Peer address="127.0.0.1"/>
    <Peer address="192.168.123.18"/>
  </Peers>
  <ParticipantIndex>auto</ParticipantIndex>
</Discovery>
```

## 5) 실행 절차 (운영 표준)

### 5-1. 터미널 A: SLAM 파이프라인 실행
```bash
source /home/jnu/isaac_ws/env_go2_slam.sh
ros2 daemon stop
ros2 launch /home/jnu/isaac_ws/go2_real/go2_slam.launch.py
```

`rtabmap_viz`까지 켜려면:
```bash
ros2 launch /home/jnu/isaac_ws/go2_real/go2_slam.launch.py use_viz:=true
```

### 5-2. 터미널 B: RViz 실행
```bash
source /home/jnu/isaac_ws/env_go2_slam.sh
rviz2 -d /home/jnu/isaac_ws/go2_sim.rviz
```

## 6) 빠른 정상 체크리스트

```bash
ros2 topic hz /utlidar/robot_odom_sync
ros2 topic hz /my_go2/color/image_raw_sync
ros2 topic hz /my_go2/depth/image_rect_raw_sync
ros2 topic hz /my_go2/color/camera_info_sync
```

- 4개 모두 Hz가 나와야 RTAB-Map 입력 동기화가 안정

```bash
ros2 run tf2_ros tf2_echo base_link my_go2_color_optical_frame
```

- TF가 지속적으로 조회되어야 함

```bash
ros2 topic echo /my_go2/depth/image_rect_raw_sync --once
```

- `encoding`이 `16UC1` 또는 `32FC1`인지 확인

## 7) 트러블슈팅 가이드

### 케이스 A: `No module named rclpy.node`
원인:
- ROS2 환경/파이썬 경로 미설정
해결:
- `/opt/ros/humble` source
- `go2_visualize.py`의 `rclpy` bridge path 주입 유지

### 케이스 B: `topic_sync`가 안 뜸
증상:
- `/topic_sync` 노드 미발견
- `_sync` 토픽 미발행
점검:
- 로그 파일 권한 (`ROS_LOG_DIR=/tmp/ros_logs`)
- 터미널 간 환경변수 불일치

### 케이스 C: RTAB-Map `Did not receive data`
원인:
- 4개 입력 토픽 중 일부 누락
- timestamp sync 실패
해결:
- `_sync` 4종 Hz 확인
- buffer 파라미터 유지 (`queue/topic_queue/sync_queue`)

### 케이스 D: `Unrecognized image encoding [passthrough]`
원인:
- depth encoding 부적합
해결:
- `go2_topic_sync.py`에서 `16UC1/32FC1`로 정규화

### 케이스 E: `TF ... not part of the same tree`
원인:
- `base_link` ↔ `my_go2_color_optical_frame` 연결 누락/지연
해결:
- `go2_topic_sync.py`의 static + periodic TF broadcast 유지
- `tf2_echo`로 연속 확인

### 케이스 F: compressed depth `uint8`
원인:
- 시각화용 depth(비정량)
해결:
- 해당 프레임 스킵, raw depth 우선 사용(현재 코드 반영됨)

## 8) 운영 팁

- 맵이 퍼지면:
  - 이동 속도/회전 속도를 낮춤
  - RTAB-Map DetectionRate를 낮춰 안정화
  - RViz PointCloud size를 줄여 실제 품질과 표시 품질을 분리

- 새 터미널에서 항상:
  - `source /home/jnu/isaac_ws/env_go2_slam.sh`

## 9) 권장 운영 다이어그램

```text
(1) 환경 통일
  모든 터미널
    -> source env_go2_slam.sh

(2) 파이프 시작
  launch go2_slam.launch.py
    -> topic_sync 자동 실행
    -> rtabmap 실행
    -> (선택) rtabmap_viz 실행

(3) 검증
  hz 4개 + tf2_echo + depth encoding

(4) RViz
  go2_sim.rviz 로 map/cloud/image 모니터링
```

```text
장애 발생 시 의사결정 트리

sync 토픽 없음?
  -> topic_sync 프로세스/환경 확인

sync는 있음, rtabmap 에러?
  -> depth encoding 확인(16UC1/32FC1)
  -> TF(base_link->camera) 확인

맵 품질 낮음?
  -> DetectionRate/이동속도/표시 크기 조정
```

---

최종 상태 요약:
- 현재 파이프라인은 `_sync` 기반으로 동작하도록 정리되어 있으며,
- 핵심 실패 원인(환경 불일치, depth passthrough, TF 단절)을 코드/설정 양쪽에서 대응한 상태입니다.
