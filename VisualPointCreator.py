import omni.usd
import omni.ui as ui
import omni.kit.commands
from pxr import UsdGeom, Gf

stage = omni.usd.get_context().get_stage()
usd_context = omni.usd.get_context()
selection = usd_context.get_selection()

window = None
marker_count = 0
last_created_path = None


COLORS = {
    "Red": Gf.Vec3f(1.0, 0.0, 0.0),
    "Green": Gf.Vec3f(0.0, 1.0, 0.0),
    "Blue": Gf.Vec3f(0.0, 0.0, 1.0),
    "Yellow": Gf.Vec3f(1.0, 1.0, 0.0),
    "Cyan": Gf.Vec3f(0.0, 1.0, 1.0),
    "White": Gf.Vec3f(1.0, 1.0, 1.0),
    "Purple": Gf.Vec3f(0.7, 0.0, 1.0),
}


def clear_markers():
    global marker_count, last_created_path

    if stage.GetPrimAtPath("/World/VisualPoints"):
        stage.RemovePrim("/World/VisualPoints")

    marker_count = 0
    last_created_path = None
    print("Cleared visual points.")


def focus_on_prim(path):
    prim = stage.GetPrimAtPath(path)

    if not prim.IsValid():
        print("Invalid prim:", path)
        return

    selection.set_selected_prim_paths([path], True)

    try:
        omni.kit.commands.execute(
            "FramePrimsCommand",
            prim_to_move=path,
            prims_to_frame=[path],
            zoom=0.7
        )
        print("Focused on:", path)

    except Exception as e:
        print("Auto focus failed:", e)
        print("Point is selected. Press F in viewport.")


def create_marker(x, y, z, size, color_name):
    global marker_count, last_created_path

    root = "/World/VisualPoints"

    if not stage.GetPrimAtPath(root):
        UsdGeom.Xform.Define(stage, root)

    marker_path = f"{root}/Point_{marker_count}"

    sphere = UsdGeom.Sphere.Define(stage, marker_path)
    sphere.AddTranslateOp().Set(Gf.Vec3f(x, y, z))
    sphere.GetRadiusAttr().Set(size)

    color = COLORS.get(color_name, COLORS["Red"])
    sphere.GetDisplayColorAttr().Set([color])

    last_created_path = marker_path
    marker_count += 1

    selection.set_selected_prim_paths([marker_path], True)

    print(f"Created {color_name} point: {marker_path} at X={x}, Y={y}, Z={z}")

    return marker_path


def create_gui():
    global window

    window = ui.Window("Visual Point Creator", width=400, height=470)

    with window.frame:
        with ui.VStack(spacing=8, padding=10):

            ui.Label("Create Visual Point From X / Y / Z")

            with ui.HStack(height=28):
                ui.Label("X", width=80)
                x_field = ui.FloatField(width=120)
                x_field.model.set_value(0.0)

            with ui.HStack(height=28):
                ui.Label("Y", width=80)
                y_field = ui.FloatField(width=120)
                y_field.model.set_value(0.0)

            with ui.HStack(height=28):
                ui.Label("Z", width=80)
                z_field = ui.FloatField(width=120)
                z_field.model.set_value(0.25)

            with ui.HStack(height=28):
                ui.Label("Size", width=80)
                size_field = ui.FloatField(width=120)
                size_field.model.set_value(0.5)

            ui.Label("Color")
            color_combo = ui.ComboBox(0, *list(COLORS.keys()))

            last_label = ui.Label("Last Point: None", height=35)

            def get_selected_color():
                index = color_combo.model.get_item_value_model().as_int
                return list(COLORS.keys())[index]

            def on_create():
                path = create_marker(
                    x_field.model.get_value_as_float(),
                    y_field.model.get_value_as_float(),
                    z_field.model.get_value_as_float(),
                    size_field.model.get_value_as_float(),
                    get_selected_color()
                )

                last_label.text = f"Last Point: {path}"

            def on_focus():
                if last_created_path:
                    focus_on_prim(last_created_path)
                else:
                    print("No point created yet.")

            ui.Button("Create Visual Point", clicked_fn=on_create)
            ui.Button("Focus On Last Point", clicked_fn=on_focus)
            ui.Button("Clear Visual Points", clicked_fn=clear_markers)

            ui.Spacer(height=10)

            ui.Label("Created under:")
            ui.Label("/World/VisualPoints")


create_gui()
