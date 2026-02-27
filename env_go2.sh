source /opt/ros/humble/setup.bash
source /home/jnu/isaac_ws/install/local_setup.bash

export RMW_IMPLEMENTATION=rmw_cyclonedds_cpp
export ROS_DOMAIN_ID=0
export ROS_LOCALHOST_ONLY=0
export CYCLONEDDS_URI=file:///home/jnu/cyclonedds.xml
export ROS_LOG_DIR=/tmp/ros_logs
mkdir -p /tmp/ros_logs
