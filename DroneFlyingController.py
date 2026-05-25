import omni.ui as ui
import omni.usd
import omni.kit.app
from pxr import UsdPhysics, Gf

# =========================
# Drone Flight GUI
# =========================

class DroneFlightGUI:
    def __init__(self):
        self.drone_path = "/World/cf2x"
        self.target_height = 2.0
        self.move_speed = 2.0
        self.thrust_speed = 3.0

        self.forward = False
        self.backward = False
        self.left = False
        self.right = False
        self.auto_height = True
        self.manual_thrust = False
        self.flying = False

        self.sub = omni.kit.app.get_app().get_update_event_stream().create_subscription_to_pop(
            self.update
        )

        self.window = ui.Window("Drone Flight Controller", width=330, height=420)
        with self.window.frame:
            with ui.VStack(spacing=8, padding=10):

                ui.Label("Drone Path")
                self.path_field = ui.StringField()
                self.path_field.model.set_value(self.drone_path)

                ui.Label("Target Height")
                self.height_field = ui.FloatField()
                self.height_field.model.set_value(self.target_height)

                ui.Label("Move Speed")
                self.speed_field = ui.FloatField()
                self.speed_field.model.set_value(self.move_speed)

                ui.Label("Thrust Speed")
                self.thrust_field = ui.FloatField()
                self.thrust_field.model.set_value(self.thrust_speed)

                ui.Button("Set Drone Reference", clicked_fn=self.set_drone)

                ui.Separator()

                ui.Button("Start Drone / Hover", clicked_fn=self.start_drone)
                ui.Button("Stop Drone", clicked_fn=self.stop_drone)

                ui.CheckBox(
                    model=ui.SimpleBoolModel(self.auto_height),
                    tooltip="Auto-stabilize drone at target height"
                ).model.add_value_changed_fn(self.set_auto_height)

                ui.Label("Auto Height Stabilization Enabled")

                ui.Separator()

                with ui.HStack(spacing=5):
                    ui.Button("Forward", clicked_fn=self.toggle_forward)
                
                with ui.HStack(spacing=5):
                    ui.Button("Left", clicked_fn=self.toggle_left)
                    ui.Button("Stop Move", clicked_fn=self.stop_movement)
                    ui.Button("Right", clicked_fn=self.toggle_right)

                with ui.HStack(spacing=5):
                    ui.Button("Backward", clicked_fn=self.toggle_backward)

                ui.Separator()

                ui.Button("Thrust Up ON/OFF", clicked_fn=self.toggle_thrust)

                ui.Label("Tip: Press PLAY first in Isaac Sim.")

    def set_drone(self):
        self.drone_path = self.path_field.model.get_value_as_string()
        self.target_height = self.height_field.model.get_value_as_float()
        self.move_speed = self.speed_field.model.get_value_as_float()
        self.thrust_speed = self.thrust_field.model.get_value_as_float()
        print(f"Drone set to: {self.drone_path}")

    def get_body_api(self):
        stage = omni.usd.get_context().get_stage()
        prim = stage.GetPrimAtPath(self.drone_path)

        if not prim.IsValid():
            print(f"Invalid drone path: {self.drone_path}")
            return None, None

        if not prim.HasAPI(UsdPhysics.RigidBodyAPI):
            UsdPhysics.RigidBodyAPI.Apply(prim)

        rb = UsdPhysics.RigidBodyAPI(prim)
        return prim, rb

    def set_velocity(self, velocity):
        prim, rb = self.get_body_api()
        if rb is None:
            return

        rb.CreateVelocityAttr().Set(Gf.Vec3f(*velocity))

    def get_position(self):
        stage = omni.usd.get_context().get_stage()
        prim = stage.GetPrimAtPath(self.drone_path)

        if not prim.IsValid():
            return None

        xform = prim.GetAttribute("xformOp:translate")
        if xform:
            return xform.Get()

        return None

    def start_drone(self):
        self.set_drone()
        self.flying = True
        self.auto_height = True
        print("Drone started.")

    def stop_drone(self):
        self.flying = False
        self.manual_thrust = False
        self.stop_movement()
        self.set_velocity([0, 0, 0])
        print("Drone stopped.")

    def set_auto_height(self, model):
        self.auto_height = model.get_value_as_bool()

    def toggle_forward(self):
        self.forward = not self.forward
        self.backward = False

    def toggle_backward(self):
        self.backward = not self.backward
        self.forward = False

    def toggle_left(self):
        self.left = not self.left
        self.right = False

    def toggle_right(self):
        self.right = not self.right
        self.left = False

    def stop_movement(self):
        self.forward = False
        self.backward = False
        self.left = False
        self.right = False

    def toggle_thrust(self):
        self.manual_thrust = not self.manual_thrust
        if self.manual_thrust:
            self.auto_height = False
            print("Manual thrust ON")
        else:
            print("Manual thrust OFF")

    def update(self, event):
        if not self.flying:
            return

        vx = 0
        vy = 0
        vz = 0

        if self.forward:
            vx += self.move_speed
        if self.backward:
            vx -= self.move_speed
        if self.left:
            vy += self.move_speed
        if self.right:
            vy -= self.move_speed

        if self.manual_thrust:
            vz = self.thrust_speed

        elif self.auto_height:
            pos = self.get_position()
            if pos:
                current_z = pos[2]
                error = self.target_height - current_z

                # simple height stabilizer
                vz = max(min(error * 2.0, self.thrust_speed), -self.thrust_speed)

        self.set_velocity([vx, vy, vz])


# Create GUI
drone_gui = DroneFlightGUI()