import omni.usd
import omni.ui as ui
from pxr import Usd, UsdGeom, Gf
import heapq
import random

stage = omni.usd.get_context().get_stage()
usd_context = omni.usd.get_context()
selection = usd_context.get_selection()

window = None
ignored_paths = set()
ignored_label = None

start_marker_path = None
end_marker_path = None
start_label = None
end_label = None

COLORS = {
    "Red": Gf.Vec3f(1.0, 0.0, 0.0),
    "Green": Gf.Vec3f(0.0, 1.0, 0.0),
    "Blue": Gf.Vec3f(0.0, 0.0, 1.0),
    "Yellow": Gf.Vec3f(1.0, 1.0, 0.0),
    "Cyan": Gf.Vec3f(0.0, 1.0, 1.0),
    "Purple": Gf.Vec3f(0.7, 0.0, 1.0),
    "White": Gf.Vec3f(1.0, 1.0, 1.0),
}


def float_input(label, default):
    with ui.HStack(height=28):
        ui.Label(label, width=150)
        field = ui.FloatField(width=120)
        field.model.set_value(default)
    return field


def get_world_pos(prim):
    xform = UsdGeom.Xformable(prim)
    mat = xform.ComputeLocalToWorldTransform(0)
    p = mat.ExtractTranslation()
    return Gf.Vec3d(p[0], p[1], p[2])


def get_world_bbox(prim):
    bbox_cache = UsdGeom.BBoxCache(
        Usd.TimeCode.Default(),
        [UsdGeom.Tokens.default_],
        useExtentsHint=True
    )
    bbox = bbox_cache.ComputeWorldBound(prim)
    box = bbox.ComputeAlignedBox()
    return box.GetMin(), box.GetMax()


def update_labels():
    if ignored_label:
        ignored_label.text = "Ignored Objects: None" if not ignored_paths else "Ignored Objects:\n" + "\n".join(ignored_paths)

    if start_label:
        start_label.text = f"Start Point: {start_marker_path}" if start_marker_path else "Start Point: None"

    if end_label:
        end_label.text = f"End Point: {end_marker_path}" if end_marker_path else "End Point: None"


def set_selected_as_start():
    global start_marker_path
    selected = selection.get_selected_prim_paths()

    if not selected:
        print("Select a start marker object first.")
        return

    start_marker_path = str(selected[0])
    print("Start marker set:", start_marker_path)
    update_labels()


def set_selected_as_end():
    global end_marker_path
    selected = selection.get_selected_prim_paths()

    if not selected:
        print("Select an end marker object first.")
        return

    end_marker_path = str(selected[0])
    print("End marker set:", end_marker_path)
    update_labels()


def add_selected_to_ignore():
    selected = selection.get_selected_prim_paths()

    if not selected:
        print("No object selected.")
        return

    for path in selected:
        ignored_paths.add(str(path))
        print("Added to ignore:", path)

    update_labels()


def clear_ignore_list():
    ignored_paths.clear()
    update_labels()
    print("Ignore list cleared.")


def is_ignored(prim):
    prim_path = str(prim.GetPath())
    return any(prim_path.startswith(ignored) for ignored in ignored_paths)


def clear_generated():
    if stage.GetPrimAtPath("/World/GeneratedDronePath"):
        stage.RemovePrim("/World/GeneratedDronePath")
    print("Cleared generated path.")


def collect_obstacles(obstacle_padding):
    obstacles = []

    for prim in stage.Traverse():
        path = str(prim.GetPath())

        if path.startswith("/World/GeneratedDronePath"):
            continue

        if path == start_marker_path or path == end_marker_path:
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

            except Exception as e:
                print("Failed obstacle:", prim.GetPath(), e)

    print("Total obstacles:", len(obstacles))
    return obstacles


def is_blocked(x, y, obstacles):
    for min_x, max_x, min_y, max_y in obstacles:
        if min_x <= x <= max_x and min_y <= y <= max_y:
            return True
    return False


def create_path_point(root_path, index, x, y, z, size, color):
    cube = UsdGeom.Cube.Define(stage, f"{root_path}/PathPoint_{index}")
    cube.AddTranslateOp().Set(Gf.Vec3f(x, y, z))
    cube.AddScaleOp().Set(Gf.Vec3f(size, size, 0.04))
    cube.GetDisplayColorAttr().Set([color])


def heuristic(a, b):
    return abs(a[0] - b[0]) + abs(a[1] - b[1])


def world_to_grid(x, y, min_x, min_y, step):
    return int(round((x - min_x) / step)), int(round((y - min_y) / step))


def grid_to_world(cell, min_x, min_y, step):
    return min_x + cell[0] * step, min_y + cell[1] * step


def random_astar(start, goal, min_x, max_x, min_y, max_y, step, obstacles, randomness):
    open_set = []
    heapq.heappush(open_set, (0, start))

    came_from = {}
    g_score = {start: 0}

    dirs = [
        (1, 0), (-1, 0),
        (0, 1), (0, -1),
        (1, 1), (1, -1),
        (-1, 1), (-1, -1)
    ]

    while open_set:
        _, current = heapq.heappop(open_set)

        if current == goal:
            path = [current]
            while current in came_from:
                current = came_from[current]
                path.append(current)
            path.reverse()
            return path

        random.shuffle(dirs)

        for dx, dy in dirs:
            nx = current[0] + dx
            ny = current[1] + dy

            world_x = min_x + nx * step
            world_y = min_y + ny * step

            if world_x < min_x or world_x > max_x:
                continue

            if world_y < min_y or world_y > max_y:
                continue

            if is_blocked(world_x, world_y, obstacles):
                continue

            neighbor = (nx, ny)
            move_cost = 1.4 if dx != 0 and dy != 0 else 1.0

            random_cost = random.uniform(0.0, randomness)

            new_g = g_score[current] + move_cost + random_cost

            if neighbor not in g_score or new_g < g_score[neighbor]:
                came_from[neighbor] = current
                g_score[neighbor] = new_g

                f = new_g + heuristic(neighbor, goal) + random.uniform(0.0, randomness)
                heapq.heappush(open_set, (f, neighbor))

    return []


def generate_random_path(
    area_min_x,
    area_max_x,
    area_min_y,
    area_max_y,
    point_spacing,
    obstacle_padding,
    path_height,
    point_size,
    randomness,
    color_name
):
    if not start_marker_path or not end_marker_path:
        print("Please set both Start Point and End Point.")
        return

    start_prim = stage.GetPrimAtPath(start_marker_path)
    end_prim = stage.GetPrimAtPath(end_marker_path)

    if not start_prim.IsValid() or not end_prim.IsValid():
        print("Start or End marker is invalid.")
        return

    start_pos = get_world_pos(start_prim)
    end_pos = get_world_pos(end_prim)

    clear_generated()
    obstacles = collect_obstacles(obstacle_padding)

    start = world_to_grid(start_pos[0], start_pos[1], area_min_x, area_min_y, point_spacing)
    goal = world_to_grid(end_pos[0], end_pos[1], area_min_x, area_min_y, point_spacing)

    path = random_astar(
        start,
        goal,
        area_min_x,
        area_max_x,
        area_min_y,
        area_max_y,
        point_spacing,
        obstacles,
        randomness
    )

    if not path:
        print("Path was not created. Try again with lower padding or larger area.")
        return

    root_path = "/World/GeneratedDronePath"
    UsdGeom.Xform.Define(stage, root_path)

    color = COLORS.get(color_name, COLORS["Red"])

    for i, cell in enumerate(path):
        x, y = grid_to_world(cell, area_min_x, area_min_y, point_spacing)
        create_path_point(root_path, i, x, y, path_height, point_size, color)

    print("Generated random obstacle-avoiding path points:", len(path))


def create_gui():
    global window, ignored_label, start_label, end_label

    window = ui.Window("Random Drone Path Generator", width=460, height=820)

    with window.frame:
        with ui.VStack(spacing=8):

            ui.Label("Random Drone Path Generator", height=30)

            area_min_x = float_input("Area Min X", -20)
            area_max_x = float_input("Area Max X", 20)
            area_min_y = float_input("Area Min Y", -20)
            area_max_y = float_input("Area Max Y", 20)

            point_spacing = float_input("Point Spacing", 1.0)
            obstacle_padding = float_input("Obstacle Padding", 0.8)
            path_height = float_input("Path Height", 0.25)
            point_size = float_input("Point Size", 0.25)
            randomness = float_input("Randomness", 3.0)

            ui.Label("Path Color")
            color_combo = ui.ComboBox(0, *list(COLORS.keys()))

            ui.Spacer(height=10)

            ui.Button("Set Selected As Start Point", clicked_fn=set_selected_as_start)
            start_label = ui.Label("Start Point: None", height=35)

            ui.Button("Set Selected As End Point", clicked_fn=set_selected_as_end)
            end_label = ui.Label("End Point: None", height=35)

            ui.Spacer(height=10)

            ui.Button("Add Selected Object To Ignore", clicked_fn=add_selected_to_ignore)
            ui.Button("Clear Ignore List", clicked_fn=clear_ignore_list)

            ignored_label = ui.Label("Ignored Objects: None", height=100)

            def get_selected_color():
                index = color_combo.model.get_item_value_model().as_int
                return list(COLORS.keys())[index]

            def on_generate():
                generate_random_path(
                    area_min_x.model.get_value_as_float(),
                    area_max_x.model.get_value_as_float(),
                    area_min_y.model.get_value_as_float(),
                    area_max_y.model.get_value_as_float(),
                    point_spacing.model.get_value_as_float(),
                    obstacle_padding.model.get_value_as_float(),
                    path_height.model.get_value_as_float(),
                    point_size.model.get_value_as_float(),
                    randomness.model.get_value_as_float(),
                    get_selected_color()
                )

            ui.Button("Generate Random Path", clicked_fn=on_generate)
            ui.Button("Clear Generated Path", clicked_fn=clear_generated)

            ui.Spacer(height=10)

            ui.Label("Higher Randomness = different route")
            ui.Label("Recommended Randomness: 2 to 8")
            ui.Label("Path root: /World/GeneratedDronePath")


create_gui()
