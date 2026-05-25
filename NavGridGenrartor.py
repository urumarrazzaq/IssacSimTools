import omni.usd
import omni.ui as ui
from pxr import Usd, UsdGeom, Gf

stage = omni.usd.get_context().get_stage()
usd_context = omni.usd.get_context()
selection = usd_context.get_selection()

window = None
ignored_paths = set()
ignored_label = None


# -------------------------
# UI FIELD HELPER
# -------------------------
def float_input(label, default):
    with ui.HStack(height=28):
        ui.Label(label, width=140)
        field = ui.FloatField(width=120)
        field.model.set_value(default)
    return field


# -------------------------
# USD HELPERS
# -------------------------
def get_world_bbox(prim):
    bbox_cache = UsdGeom.BBoxCache(
        Usd.TimeCode.Default(),
        [UsdGeom.Tokens.default_],
        useExtentsHint=True
    )
    bbox = bbox_cache.ComputeWorldBound(prim)
    box = bbox.ComputeAlignedBox()
    return box.GetMin(), box.GetMax()


def update_ignore_text():
    global ignored_label

    if ignored_label:
        if len(ignored_paths) == 0:
            ignored_label.text = "Ignored Objects: None"
        else:
            ignored_label.text = "Ignored Objects:\n" + "\n".join(ignored_paths)


def add_selected_to_ignore():
    selected_paths = selection.get_selected_prim_paths()

    if not selected_paths:
        print("No object selected. Select object from Stage panel first.")
        return

    for path in selected_paths:
        ignored_paths.add(str(path))
        print("Added to ignore:", path)

    update_ignore_text()


def clear_ignore_list():
    ignored_paths.clear()
    update_ignore_text()
    print("Ignore list cleared.")


def is_ignored(prim):
    prim_path = str(prim.GetPath())

    for ignored in ignored_paths:
        if prim_path.startswith(ignored):
            return True

    return False


def clear_generated():
    if stage.GetPrimAtPath("/World/GeneratedWalkableArea"):
        stage.RemovePrim("/World/GeneratedWalkableArea")

    print("Cleared generated area.")


def is_inside_obstacle(x, y, obstacles):
    for min_x, max_x, min_y, max_y in obstacles:
        if min_x <= x <= max_x and min_y <= y <= max_y:
            return True
    return False


def collect_obstacles(obstacle_padding):
    obstacles = []

    print("Collecting obstacles...")

    for prim in stage.Traverse():
        path = str(prim.GetPath())

        if path.startswith("/World/GeneratedWalkableArea"):
            continue

        if is_ignored(prim):
            continue

        if prim.IsA(UsdGeom.Capsule) or prim.IsA(UsdGeom.Mesh) or prim.IsA(UsdGeom.Cube):
            try:
                min_pt, max_pt = get_world_bbox(prim)

                obstacles.append((
                    min_pt[0] - obstacle_padding,
                    max_pt[0] + obstacle_padding,
                    min_pt[1] - obstacle_padding,
                    max_pt[1] + obstacle_padding
                ))

                print("Obstacle:", prim.GetPath())

            except Exception as e:
                print("Failed:", prim.GetPath(), e)

    print("Total obstacles:", len(obstacles))
    return obstacles


def generate_walkable_area(
    area_min_x,
    area_max_x,
    area_min_y,
    area_max_y,
    grid_size,
    obstacle_padding,
    path_height,
    cell_size
):
    clear_generated()

    obstacles = collect_obstacles(obstacle_padding)

    UsdGeom.Xform.Define(stage, "/World/GeneratedWalkableArea")

    count = 0
    x = area_min_x

    while x <= area_max_x:
        y = area_min_y

        while y <= area_max_y:
            if not is_inside_obstacle(x, y, obstacles):
                cube_path = f"/World/GeneratedWalkableArea/PathCell_{count}"
                cube = UsdGeom.Cube.Define(stage, cube_path)

                cube.AddTranslateOp().Set(Gf.Vec3f(x, y, path_height))
                cube.AddScaleOp().Set(Gf.Vec3f(cell_size, cell_size, 0.035))
                cube.GetDisplayColorAttr().Set([Gf.Vec3f(0.0, 1.0, 0.0)])

                count += 1

            y += grid_size

        x += grid_size

    print("Generated walkable cells:", count)


# -------------------------
# GUI
# -------------------------
def create_gui():
    global window, ignored_label

    window = ui.Window("Drone Path Generator", width=420, height=620)

    with window.frame:
        with ui.VStack(spacing=8):

            ui.Label("Drone Walkable Area Generator", height=30)

            area_min_x = float_input("Area Min X", -10)
            area_max_x = float_input("Area Max X", 10)

            area_min_y = float_input("Area Min Y", -10)
            area_max_y = float_input("Area Max Y", 10)

            grid_size = float_input("Grid Size", 1.0)
            obstacle_padding = float_input("Obstacle Padding", 0.8)
            path_height = float_input("Path Height", 0.25)
            cell_size = float_input("Cell Size", 0.35)

            ui.Spacer(height=10)

            ui.Button("Add Selected Object To Ignore", clicked_fn=add_selected_to_ignore)
            ui.Button("Clear Ignore List", clicked_fn=clear_ignore_list)

            ignored_label = ui.Label("Ignored Objects: None", height=100)

            ui.Spacer(height=10)

            def on_generate():
                generate_walkable_area(
                    area_min_x.model.get_value_as_float(),
                    area_max_x.model.get_value_as_float(),
                    area_min_y.model.get_value_as_float(),
                    area_max_y.model.get_value_as_float(),
                    grid_size.model.get_value_as_float(),
                    obstacle_padding.model.get_value_as_float(),
                    path_height.model.get_value_as_float(),
                    cell_size.model.get_value_as_float()
                )

            ui.Button("Generate Walkable Area", clicked_fn=on_generate)
            ui.Button("Clear Generated Path", clicked_fn=clear_generated)

            ui.Spacer(height=10)

            ui.Label("Steps:")
            ui.Label("1. Select GroundPlane or CollisionMesh in Stage")
            ui.Label("2. Click Add Selected Object To Ignore")
            ui.Label("3. Generate walkable area")


create_gui()
