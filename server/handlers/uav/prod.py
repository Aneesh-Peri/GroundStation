from __future__ import annotations
import json
import logging
import math
import os
import typing
from typing import Optional

from dronekit import connect, Command, VehicleMode, Vehicle
from pymavlink import mavutil as uavutil

from utils.errors import GeneralError, InvalidRequestError, InvalidStateError
from utils.decorators import decorate_all_functions, log, wait_for_param_load

if typing.TYPE_CHECKING:
    from groundstation import GroundStation

BAUDRATE = 57600

COMMANDS = {
    # Takeoff will be initiated using a Flight Mode
    # "TAKEOFF": uavutil.mavlink.MAV_CMD_NAV_TAKEOFF,
    "WAYPOINT": uavutil.mavlink.MAV_CMD_NAV_WAYPOINT,
    "LAND": uavutil.mavlink.MAV_CMD_NAV_LAND,
    "GEOFENCE": uavutil.mavlink.MAV_CMD_NAV_FENCE_POLYGON_VERTEX_INCLUSION,
}


def pixhawk_stats(vehicle):
    vehicle.wait_ready("autopilot_version")
    print("\nGet all vehicle attribute values:")
    print(f" Autopilot Firmware version: {vehicle.version}")
    print(f"   Major version number: {vehicle.version.major}")
    print(f"   Minor version number: {vehicle.version.minor}")
    print(f"   Patch version number: {vehicle.version.patch}")
    print(f"   Release type: {vehicle.version.release_type()}")
    print(f"   Release version: {vehicle.version.release_version()}")
    print(f"   Stable release?: {vehicle.version.is_stable()}")
    print(" Autopilot capabilities")
    print(f"   Supports MISSION_FLOAT message type: {vehicle.capabilities.mission_float}")
    print(f"   Supports PARAM_FLOAT message type: {vehicle.capabilities.param_float}")
    print(f"   Supports MISSION_INT message type: {vehicle.capabilities.mission_int}")
    print(f"   Supports COMMAND_INT message type: {vehicle.capabilities.command_int}")
    print(f"   Supports PARAM_UNION message type: {vehicle.capabilities.param_union}")
    print(f"   Supports ftp for file transfers: {vehicle.capabilities.ftp}")
    print(f"   Supports commanding attitude offboard: {vehicle.capabilities.set_attitude_target}")
    print(
        f"   Supports commanding position and velocity targets in local NED frame: {vehicle.capabilities.set_attitude_target_local_ned}"
    )
    print(
        f"   Supports set position + velocity targets in global scaled integers: {vehicle.capabilities.set_altitude_target_global_int}"
    )
    print(f"   Supports terrain protocol / data handling: {vehicle.capabilities.terrain}")
    print(f"   Supports direct actuator control: {vehicle.capabilities.set_actuator_target}")
    print(f"   Supports the flight termination command: {vehicle.capabilities.flight_termination}")
    print(f"   Supports mission_float message type: {vehicle.capabilities.mission_float}")
    print(f"   Supports onboard compass calibration: {vehicle.capabilities.compass_calibration}")
    print(f" Global Location: {vehicle.location.global_frame}")
    print(f" Global Location (relative altitude): {vehicle.location.global_relative_frame}")
    print(f" Local Location: {vehicle.location.local_frame}")
    print(f" Attitude: {vehicle.attitude}")
    print(f" Velocity: {vehicle.velocity}")
    print(f" GPS: {vehicle.gps_0}")
    print(f" Gimbal status: {vehicle.gimbal}")
    print(f" Battery: {vehicle.battery}")
    print(f" EKF OK?: {vehicle.ekf_ok}")
    print(f" Last Heartbeat: {vehicle.last_heartbeat}")
    print(f" Rangefinder: {vehicle.rangefinder}")
    print(f" Rangefinder distance: {vehicle.rangefinder.distance}")
    print(f" Rangefinder voltage: {vehicle.rangefinder.voltage}")
    print(f" Heading: {vehicle.heading}")
    print(f" Is Armable?: {vehicle.is_armable}")
    print(f" System status: {vehicle.system_status.state}")
    print(f" Groundspeed: {vehicle.groundspeed}")  # settable
    print(f" Airspeed: {vehicle.airspeed}")  # settable
    print(f" Mode: {vehicle.mode.name}")  # settable
    print(f" Armed: {vehicle.armed}")  # settable


def readmission(filename):
    """
    Load a mission from a file into a list.

    This function is used by upload_mission().
    """
    print(f"Reading mission from file: {filename}\n")
    missionlist = []
    with open(filename, "r", encoding="utf-8") as f:
        for i, line in enumerate(f):
            if i == 0:
                if not line.startswith("QGC WPL 110"):
                    raise Exception("File is not supported WP version")
            else:
                linearray = line.split("\t")
                ln_currentwp = int(linearray[1])
                ln_frame = int(linearray[2])
                ln_command = int(linearray[3])
                ln_param1 = float(linearray[4])
                ln_param2 = float(linearray[5])
                ln_param3 = float(linearray[6])
                ln_param4 = float(linearray[7])
                ln_param5 = float(linearray[8])
                ln_param6 = float(linearray[9])
                ln_param7 = float(linearray[10])
                ln_autocontinue = int(linearray[11].strip())
                cmd = Command(
                    0,
                    0,
                    0,
                    ln_frame,
                    ln_command,
                    ln_currentwp,
                    ln_autocontinue,
                    ln_param1,
                    ln_param2,
                    ln_param3,
                    ln_param4,
                    ln_param5,
                    ln_param6,
                    ln_param7,
                )
                missionlist.append(cmd)
    return missionlist


def download_mission(vehicle):
    """
    Downloads the current mission and returns it in a list.
    It is used in save_mission() to get the file information to save.
    """
    missionlist = []
    cmds = vehicle.commands
    cmds.download()
    cmds.wait_ready()
    for cmd in cmds:
        missionlist.append(cmd)
    return missionlist


@decorate_all_functions(log, logging.getLogger("groundstation"))
class UAVHandler:
    mph = 2.23694
    ft = 3.28084

    wait_for = ("gps_0", "armed", "mode", "attitude")  # params

    def __init__(self, gs, config):
        self.logger = logging.getLogger("groundstation")
        self.gs: GroundStation = gs
        self.config = config
        self.port = self.config["uav"]["telemetry"]["port"]
        self.serial = self.config["uav"]["telemetry"]["serial"]
        self.update_thread = None
        self.vehicle: Vehicle | None = None
        (
            self.altitude,
            self.altitude_global,
            self.orientation,
            self.ground_speed,
            self.air_speed,
            self.dist_to_wp,
            self.battery,
            self.lat,
            self.lon,
            self.connection,
            self.waypoint,
            self.waypoints,
            self.waypoint_index,
            self.temperature,
            self.params,
            self.gps,
        ) = [None] * 16
        self.mode = VehicleMode("MANUAL")
        self.commands = []
        self.armed = False
        self.status = "STANDBY"
        print("╠ CREATED UAV HANDLER")
        self.logger.info("CREATED UAV HANDLER")

    # Basic Methods

    def connect(self):
        try:
            if self.serial:
                self.vehicle = connect(self.port, wait_ready=self.wait_for, baud=BAUDRATE)
            else:
                self.vehicle = connect(self.port, wait_ready=self.wait_for)
            pixhawk_stats(self.vehicle)
            self.update()
            print("╠ INITIALIZED UAV HANDLER")
            self.logger.info("INITIALIZED UAV HANDLER")
            return {}
        except Exception as e:
            raise GeneralError(str(e)) from e

    def update(self):
        try:
            # Global Relative Frame uses absolute Latitude/Longitude and relative Altitude
            loc = self.vehicle.location.global_relative_frame
            rpy = self.vehicle.attitude  # Roll, Pitch, Yaw
            battery = self.vehicle.battery
            self.altitude = loc.alt * self.ft
            self.altitude_global = self.vehicle.location.global_frame.alt * self.ft
            self.orientation = dict(
                yaw=rpy.yaw * 180 / math.pi,
                roll=rpy.roll * 180 / math.pi,
                pitch=rpy.pitch * 180 / math.pi,
            )
            self.orientation["yaw"] += 360 if self.orientation["yaw"] < 0 else 0
            self.ground_speed = self.vehicle.groundspeed * self.mph
            self.air_speed = self.vehicle.airspeed * self.mph
            self.battery = battery.voltage  # * 0.001  # Millivolts to volts?
            self.gps = self.vehicle.gps_0
            self.connection = [self.gps.eph, self.gps.epv, self.gps.satellites_visible]
            self.lat = loc.lat
            self.lon = loc.lon
            if not self.waypoints:
                self.waypoints = self.gs.interop.get_data("waypoints")
                self.waypoints = self.waypoints["result"]
                self.waypoint_index = 1 % len(self.waypoints)
            x_dist = self.waypoints[self.waypoint_index]["latitude"] - self.lat
            y_dist = self.waypoints[self.waypoint_index]["longitude"] - self.lon
            dist = math.sqrt(x_dist**2 + y_dist**2)  # Angular distance
            # Conversion from decimal degrees to miles
            x_dist_ft = x_dist * (math.cos(self.lat * math.pi / 180) * 69.172) * 5280
            y_dist_ft = y_dist * 69.172 * 5280
            self.dist_to_wp = math.sqrt(x_dist_ft**2 + y_dist_ft**2)  # Distance in miles
            if dist <= 0.0001:  # Arbitrary value
                self.waypoint_index = (self.waypoint_index + 1) % len(self.waypoints)
            self.waypoint = [self.waypoint_index + 1, self.dist_to_wp]
            self.mode = self.vehicle.mode
            self.armed = self.vehicle.armed
            return {}
        except Exception as e:
            raise GeneralError(str(e)) from e

    def quick(self):
        try:
            self.update()
            return {
                "result": {
                    "altitude": self.altitude,
                    "altitude_global": self.altitude_global,
                    "orientation": self.orientation,
                    "lat": self.lat,
                    "lon": self.lon,
                    "ground_speed": self.ground_speed,
                    "air_speed": self.air_speed,
                    "battery": self.battery,
                    "waypoint": self.waypoint,
                    "connection": self.connection,
                }
            }
        except Exception as e:
            raise GeneralError(str(e)) from e

    def stats(self):
        return {
            "result": {
                "quick": self.quick()["result"],
                "mode": self.mode.name,
                "commands": [cmd.to_dict() for cmd in self.vehicle.commands],
                "armed": self.get_armed()["result"],
                "status": self.vehicle.system_status.state,
            }
        }

    # Setup

    def set_home(self):
        try:
            cmds = self.vehicle.commands
            cmds.download()
            cmds.wait_ready()
            self.vehicle.home_location = self.vehicle.location.global_frame
            cmds.upload()
            return {}
        except Exception as e:
            raise GeneralError(str(e)) from e

    def calibrate(self):
        try:
            self.vehicle.send_calibrate_accelerometer(simple=True)
            self.vehicle.send_calibrate_barometer()
            self.vehicle.send_calibrate_gyro()
            # self.vehicle.send_calibrate_magnetometer()
            # self.vehicle.send_calibrate_vehicle_level()
            return {}
        except Exception as e:
            raise GeneralError(str(e)) from e

    def restart(self):
        try:
            self.vehicle.reboot()
            return {}
        except Exception as e:
            raise GeneralError(str(e)) from e

    def channels(self):
        try:
            return {"result": self.vehicle.channels}
        except Exception as e:
            raise GeneralError(str(e)) from e

    # Flight Mode

    def set_flight_mode(self, flightmode):
        try:
            self.mode = self.vehicle.mode = VehicleMode(flightmode)
            return {}
        except Exception as e:
            raise GeneralError(str(e)) from e

    def get_flight_mode(self):
        try:
            self.mode = self.vehicle.mode
            return {"result": self.mode.name}
        except Exception as e:
            raise GeneralError(str(e)) from e

    # Parameters

    @wait_for_param_load
    def get_param(self, key):
        try:
            return {"result": self.vehicle.parameters[key]}
        except Exception as e:
            raise GeneralError(str(e)) from e

    @wait_for_param_load
    def get_params(self):
        try:
            return {
                "result": dict(
                    (keys, values) for keys, values in tuple(self.vehicle.parameters.items())
                )
            }
        except Exception as e:
            raise GeneralError(str(e)) from e

    @wait_for_param_load
    def set_param(self, key, value):
        try:
            print(float(value))
        except ValueError as e:
            raise InvalidRequestError("Parameter Value cannot be converted to float") from e
        try:
            self.vehicle.parameters[key] = float(value)
            return {}
        except Exception as e:
            raise GeneralError(str(e)) from e

    @wait_for_param_load
    def set_params(self, **kwargs):
        try:
            for key, value in kwargs.items():
                try:
                    float(value)
                except ValueError as e:
                    raise InvalidRequestError(
                        "Parameter Value cannot be converted to float"
                    ) from e
                self.vehicle.parameters[key] = value
            return {}
        except Exception as e:
            raise GeneralError(str(e)) from e

    @wait_for_param_load
    def save_params(self):
        try:
            with open(
                os.path.join(os.path.dirname(os.path.abspath(__file__)), "uav_params.json"),
                "w",
                encoding="utf-8",
            ) as file:
                json.dump(self.vehicle.parameters, file)
            return {}
        except Exception as e:
            raise GeneralError(str(e)) from e

    @wait_for_param_load
    def load_params(self):
        try:
            with open(
                os.path.join(os.path.dirname(os.path.abspath(__file__)), "uav_params.json"),
                "r",
                encoding="utf-8",
            ) as file:
                self.vehicle.parameters = json.load(file)
            return {}
        except Exception as e:
            raise GeneralError(str(e)) from e

    # Commands (Mission)

    def get_commands(self):
        try:
            cmds = self.vehicle.commands
            cmds.download()
            cmds.wait_ready()
            return {"result": [cmd.to_dict() for cmd in cmds]}
        except Exception as e:
            raise GeneralError(str(e)) from e

    def insert_command(self, command, lat, lon, alt):
        if command not in COMMANDS:
            raise InvalidRequestError("Invalid Command Name")
        try:
            cmds = self.vehicle.commands
            cmds.download()
            cmds.wait_ready()
            if command == "LAND":
                home = self.vehicle.home_location
                new_cmd = Command(
                    0,
                    0,
                    0,
                    uavutil.mavlink.MAV_FRAME_GLOBAL_RELATIVE_ALT,
                    COMMANDS[command],
                    0,
                    0,
                    0,
                    0,
                    0,
                    0,
                    home.lat,
                    home.lon,
                    home.alt,
                )
                cmds.add(new_cmd)
                cmds.upload()
                self.jump_to_command(cmds.count)
            else:
                new_cmd = Command(
                    0,
                    0,
                    0,
                    uavutil.mavlink.MAV_FRAME_GLOBAL_RELATIVE_ALT,
                    COMMANDS[command],
                    0,
                    0,
                    0,
                    0,
                    0,
                    0,
                    lat,
                    lon,
                    alt,
                )
                cmds.add(new_cmd)
                cmds.upload()
            return {}
        except Exception as e:
            raise GeneralError(str(e)) from e

    def jump_to_command(self, command: int):
        if not self.vehicle:
            raise InvalidRequestError("Vehicle not connected")
        try:
            self.vehicle.commands.next = command
            self.vehicle.commands.upload()
            return {}
        except Exception as e:
            raise GeneralError(str(e)) from e

    def write_commands(self):
        """
        Upload a mission from a file.
        """
        try:
            missionlist = readmission(
                os.path.join(os.path.dirname(os.path.abspath(__file__)), "uav_mission.txt")
            )
            cmds = self.vehicle.commands
            cmds.clear()
            for command in missionlist:
                cmds.add(command)
            cmds.upload()
            return {}
        except Exception as e:
            raise GeneralError(str(e)) from e

    def load_commands(self):
        """
        Save a mission in the Waypoint file format
        (https://qgroundcontrol.org/mavlink/waypoint_protocol#waypoint_file_format).
        """
        try:
            missionlist = download_mission(self.vehicle)
            output = "QGC WPL 110\n"
            for cmd in missionlist:
                commandline = (
                    f"{cmd.seq}\t{cmd.current}\t{cmd.frame}\t{cmd.command}\t"
                    f"{cmd.param1}\t{cmd.param2}\t{cmd.param3}\t{cmd.param4}\t{cmd.x}\t"
                    f"{cmd.y}\t{cmd.z}\t{cmd.autocontinue}\n"
                )
                output += commandline
            with open(
                os.path.join(os.path.dirname(os.path.abspath(__file__)), "uav_mission.txt"),
                "w",
                encoding="utf-8",
            ) as file_:
                file_.write(output)
            return {}
        except Exception as e:
            raise GeneralError(str(e)) from e

    def clear_commands(self):
        try:
            self.vehicle.commands.clear()
            self.vehicle.commands.upload()
            return {}
        except Exception as e:
            raise GeneralError(str(e)) from e

    # Armed

    def get_armed(self):
        try:
            if self.vehicle.armed:
                return {"result": "ARMED"}
            elif self.vehicle.is_armable:
                return {"result": "DISARMED (ARMABLE)"}
            else:
                return {"result": "DISARMED (NOT ARMABLE)"}
        except Exception as e:
            raise GeneralError(str(e)) from e

    def arm(self):
        try:
            if not self.vehicle.is_armable:
                self.logger.important("Vehicle is not armable")
            self.vehicle.arm(wait=True, timeout=15)  # Motors can be started
            return {}
        except TimeoutError as e:
            raise TimeoutError("Vehicle arming timed out") from e
        except InvalidStateError as e:
            raise InvalidStateError(str(e)) from e
        except Exception as e:
            # raise InvalidStateError("Vehicle is not armable")
            raise GeneralError(str(e)) from e

    def disarm(self):
        try:
            self.vehicle.disarm(wait=True, timeout=15)
            return {}
        except TimeoutError as e:
            raise TimeoutError("Vehicle disarming timed out") from e
        except Exception as e:
            raise GeneralError(str(e)) from e

    def __repr__(self):
        return "UAV Handler"
