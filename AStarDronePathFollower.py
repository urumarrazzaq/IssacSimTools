import omni.usd
import omni.ui as ui
import omni.kit.app
from pxr import UsdGeom, Gf
import re
import math


# Stop old follower controllers if they are still running
for name in [
    "drone_path_follower_gui",
    "clean_drone_follower",
    "accurate_drone_follower",
    "route_follower",
    "astar_drone_follower"
]:
    try:
        obj = globals().get(name)
        if obj:
            obj.following = False
            obj.sub.unsubscribe()
            print("Stopped old controller:", name)
    except Exception:
        pass


class AStarDroneFollower:
    def __init__(self):
        self.drone_path = "/World/ingenuity"
        self.path_root = "/World/GeneratedDronePath"

        self.flight_height = 2.0
        self.speed = 2.0
        self.reach_distance = 0.15

        self.points = []
        self.index = 0
        self.following = False
        self.loop = False

        self.sub = omni.kit.app.get_app().get_update_event_stream().create_subscription_to_pop(
            self.update
        )

        self.window = ui.Window("A* Drone Path Follower", width=420, height=430)
        self.build_ui()

    def build_ui(self):
        with self.window.frame:
            with ui.VStack(spacing=8, padding=10):

                ui.Label("A* Drone Path Follower")

                ui.Label("Drone Prim Path")
                self.drone_field = ui.StringField()
                self.drone_field.model.set_value(self.drone_path)

                ui.Label("Path Root")
                self.path_field = ui.StringField()
                self.path_field.model.set_value(self.path_root)

                ui.Label("Flight Height")
                self.height_field = ui.FloatField()
                self.height_field.model.set_value(self.flight_height)

                ui.Label("Move Speed")
                self.speed_field = ui.FloatField()
                self.speed_field.model.set_value(self.speed)

                ui.Label("Reach Distance")
                self.reach_field = ui.FloatField()
                self.reach_field.model.set_value(self.reach_distance)

                ui.Button("Load Path", clicked_fn=self.load_path)
                ui.Button("Start Follow", clicked_fn=self.start)
                ui.Button("Stop Drone", clicked_fn=self.stop)

                ui.Label("Path points must be PathPoint_0, PathPoint_1...")

    def stage(self):
        return omni.usd.get_context().get_stage()

    def get_world_pos(self, prim):
        xform = UsdGeom.Xformable(prim)
        mat = xform.ComputeLocalToWorldTransform(0)
        p = mat.ExtractTranslation()
        return Gf.Vec3d(p[0], p[1], p[2])

    def set_world_pos(self, prim, pos):
        xform = UsdGeom.Xformable(prim)

        translate_op = None
        for op in xform.GetOrderedXformOps():
            if op.GetOpType() == UsdGeom.XformOp.TypeTranslate:
                translate_op = op
                break

        if translate_op is None:
            translate_op = xform.AddTranslateOp()

        translate_op.Set(Gf.Vec3d(pos[0], pos[1], pos[2]))

    def extract_index(self, path):
        match = re.search(r"PathPoint_(\d+)", path)
        if match:
            return int(match.group(1))
        return 999999

    def load_path(self):
        self.drone_path = self.drone_field.model.get_value_as_string()
        self.path_root = self.path_field.model.get_value_as_string()
        self.flight_height = self.height_field.model.get_value_as_float()
        self.speed = self.speed_field.model.get_value_as_float()
        self.reach_distance = self.reach_field.model.get_value_as_float()

        root = self.stage().GetPrimAtPath(self.path_root)

        if not root.IsValid():
            print("Invalid path root:", self.path_root)
            return

        found = []

        for prim in self.stage().Traverse():
            path = str(prim.GetPath())

            if path.startswith(self.path_root + "/PathPoint_"):
                pos = self.get_world_pos(prim)
                found.append((self.extract_index(path), Gf.Vec3d(pos[0], pos[1], self.flight_height)))

        if not found:
            print("No PathPoint objects found.")
            return

        found.sort(key=lambda item: item[0])

        self.points = [p for _, p in found]
        self.index = 0

        print("Loaded path points:", len(self.points))

    def start(self):
        if not self.points:
            self.load_path()

        if not self.points:
            print("No path loaded.")
            return

        drone = self.stage().GetPrimAtPath(self.drone_path)
        if not drone.IsValid():
            print("Invalid drone path:", self.drone_path)
            return

        # Move drone to first path point height before starting
        start_pos = self.points[0]
        self.set_world_pos(drone, Gf.Vec3d(start_pos[0], start_pos[1], self.flight_height))

        self.index = 1
        self.following = True

        print("Drone started following A* path.")

    def stop(self):
        self.following = False
        print("Drone stopped.")

    def update(self, event):
        if not self.following:
            return

        drone = self.stage().GetPrimAtPath(self.drone_path)

        if not drone.IsValid():
            print("Invalid drone path.")
            self.stop()
            return

        if self.index >= len(self.points):
            if self.loop:
                self.index = 0
            else:
                print("Path finished.")
                self.stop()
                return

        current = self.get_world_pos(drone)
        target = self.points[self.index]

        direction = target - current
        distance = direction.GetLength()

        if distance <= self.reach_distance:
            self.index += 1
            return

        if distance > 0.001:
            direction.Normalize()

            dt = 1.0 / 60.0
            step = self.speed * dt

            if step > distance:
                step = distance

            new_pos = current + direction * step
            self.set_world_pos(drone, new_pos)


astar_drone_follower = AStarDroneFollower()
