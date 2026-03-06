#!/usr/bin/env bash

source /opt/ros/humble/setup.bash
source /home/jnu/isaac_ws/install/local_setup.bash

export LD_LIBRARY_PATH="${LD_LIBRARY_PATH}:/home/jnu/anaconda3/envs/isaaclab/lib/python3.11/site-packages/isaacsim/exts/isaacsim.ros2.bridge/humble/lib"
export RMW_IMPLEMENTATION=rmw_cyclonedds_cpp
export ROS_DOMAIN_ID=0
export CYCLONEDDS_URI=file:///home/jnu/isaac_ws/cyclonedds.xml

