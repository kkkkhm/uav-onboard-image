import time


def diagnose(device, baud, position=False):
    from pymavlink import mavutil
    link = mavutil.mavlink_connection(device, baud=baud, source_system=254, source_component=190)
    try:
        hb = link.recv_match(type="HEARTBEAT", blocking=True, timeout=10)
        if hb is None:
            raise RuntimeError("10秒内无心跳")
        system, component = hb.get_srcSystem(), hb.get_srcComponent()
        if hb.autopilot != mavutil.mavlink.MAV_AUTOPILOT_PX4 or component != 1:
            raise RuntimeError("当前心跳不是PX4飞控组件")
        lines = [f"心跳：{system}/{component}，PX4"]
        command = mavutil.mavlink.MAV_CMD_SET_MESSAGE_INTERVAL if position else mavutil.mavlink.MAV_CMD_REQUEST_MESSAGE
        msgid = mavutil.mavlink.MAVLINK_MSG_ID_LOCAL_POSITION_NED if position else mavutil.mavlink.MAVLINK_MSG_ID_AUTOPILOT_VERSION
        link.mav.command_long_send(system, component, command, 0, msgid, 100000 if position else 0, 0, 0, 0, 0, 0)
        start = time.monotonic()
        count = bad = 0
        while time.monotonic() - start < (30 if position else 5):
            msg = link.recv_match(blocking=True, timeout=0.5)
            if msg is None:
                continue
            if msg.get_type() == "BAD_DATA":
                bad += 1
            elif msg.get_srcSystem() == system and msg.get_srcComponent() == component:
                if position and msg.get_type() == "LOCAL_POSITION_NED":
                    count += 1
                elif not position and msg.get_type() == "AUTOPILOT_VERSION":
                    return "\n".join(lines + [str(msg)])
                elif msg.get_type() == "COMMAND_ACK" and msg.command == command:
                    lines.append(str(msg))
        if position:
            lines.append(f"位置{count}条，{count/(time.monotonic()-start):.2f}Hz，BAD_DATA={bad}；不代表定位有效")
        else:
            lines.append("版本回复超时")
        return "\n".join(lines)
    finally:
        link.close()
