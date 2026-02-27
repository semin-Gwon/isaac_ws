#!/usr/bin/env python3
import rclpy
from rclpy.node import Node
from nav_msgs.msg import Odometry
from tf2_ros import TransformBroadcaster
from geometry_msgs.msg import TransformStamped

class OdomToTF(Node):
    def __init__(self):
        super().__init__('odom_to_tf')
        self.subscription = self.create_subscription(
            Odometry,
            '/utlidar/robot_odom',
            self.listener_callback,
            10)
        self.br = TransformBroadcaster(self)
        self.get_logger().info('Odom to TF broadcaster started')

    def listener_callback(self, msg):
        t = TransformStamped()

        # Read timestamp and frame_id from odom message
        t.header.stamp = msg.header.stamp
        t.header.frame_id = msg.header.frame_id if msg.header.frame_id else "odom"
        t.child_frame_id = msg.child_frame_id if msg.child_frame_id else "base_link"

        # Copy translation
        t.transform.translation.x = msg.pose.pose.position.x
        t.transform.translation.y = msg.pose.pose.position.y
        t.transform.translation.z = msg.pose.pose.position.z

        # Copy rotation
        t.transform.rotation = msg.pose.pose.orientation

        # Broadcast the transform
        self.br.sendTransform(t)

def main(args=None):
    rclpy.init(args=args)
    node = OdomToTF()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    node.destroy_node()
    rclpy.shutdown()

if __name__ == '__main__':
    main()
