# Isaac Sim & ROS 2 작업 현황

> [!NOTE]
> 이 파일은 작업의 진행 상황을 트래킹하기 위한 체크리스트입니다.

## 1. SLAM 및 내비게이션 (SLAM & Navigation)

- [x] **Publisher 오류 수정 (`slam_ros.py`)**
  - [x] `Go2SlamPublisher` 클래스 `Articulation` 속성 오류(`AttributeError`) 해결
  - [x] Isaac Sim vs Isaac Lab `Articulation` 객체 호환성 수정
  - [x] Odom 및 TF 데이터 발행 정상화 확인

- [x] **Launch 파일 설정 (`go2_slam.launch.py`)**
  - [x] RTAB-Map 및 `robot_localization` 노드 구성 완료
  - [x] 센서 토픽 리매핑 (`/camera/image/raw` 등) 및 파라미터 튜닝
  - [x] TF 트리 연결 및 좌표계 설정

- [x] **SLAM 환경 스크립트 (`slam_sim.launch.py`)**
  - [x] 가상 환경(USD) 로드 및 시뮬레이션 초기화 로직 구현
  - [x] ROS 2 브릿지 연결 확인

- [ ] **SLAM 보행 스크립트 환경 추가 (`slam_walk.py`)**
  - [ ] 보행 알고리즘과 SLAM 환경 통합
  - [ ] `slam_env.usd` 로드 및 로봇 스폰 위치 조정

- [ ] **RTAB-Map 3D 시각화 문제 해결**
  - [ ] RTAB-Map Viz에서 3D 클라우드 포인트가 보이지 않는 문제 디버깅
  - [ ] 토픽 구독 상태(`rtabmap/cloud_map`) 및 메시지 타입 확인

## 2. 카메라 및 센서 데이터 처리 (Camera & Sensors)

- [x] **카메라 피드 디버깅 (`slam_cam.py`, `face_cam.py`)**
  - [x] `omni.isaac.sensor` 모듈 로드 에러(`ModuleNotFoundError`) 해결
  - [x] `CreateVideoSource` 및 UI 위젯 연동
  - [x] 가상 스크린이 로봇의 `base_link`를 따라다니도록 트래킹 구현

- [x] **토픽 동기화 (`go2_topic_sync.py`)**
  - [x] Lidar, Camera, IMU 데이터의 타임스탬프(`ApproximateTimeSynchronizer`) 동기화 구현
  - [x] 동기화된 메시지 재발행(`republish`) 로직 검증

- [x] **좌표계 변환 (`go2_odom_to_tf.py`)**
  - [x] Odometry 토픽을 구독하여 `tf` 메시지로 변환 및 브로드캐스팅
  - [x] `odom` -> `base_link` 트랜스폼 발행 확인

## 3. 시각화 및 유틸리티 (Visualization & Utilities)

- [x] **로봇 시각화 (`go2_visualize.py`)**
  - [x] 실시간 로봇 관절 상태(`joint_states`) 시각화
  - [x] 센서 데이터 스트림 모니터링 기능

- [x] **Point Cloud 임포트 (`go2_import_ply.py`)**
  - [x] `.ply` 파일 파싱 및 Open3D/Isaac Sim 데이터 변환
  - [x] Isaac Sim 씬(Scene)에 Point Cloud 로드 및 배치

- [x] **Debug rotation limit issue**
    - [x] Fix RMW implementation (unset env var, rely on Isaac Sim internal)
    - [x] Implement Yaw Integrator (Discarded)
    - [x] Align config with `go2_sim.py` (Velocity Control + Infinite Resampling)
    - [x] Fix missing `my_slam_env.py` (Replicated logic inline)
    - [x] Fix `cli_args` and `configclass` import errors
    - [/] Debug clockwise rotation (E key) stopping issue

- [x] **ROS 로그 정리**
  - [x] 디스크 공간 부족 경고 해결을 위한 `rosclean purge` 수행
