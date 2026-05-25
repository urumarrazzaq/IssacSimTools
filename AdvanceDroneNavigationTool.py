import omni.usd
import omni.ui as ui
from pxr import Usd, UsdGeom, Gf
import heapq, random, json, os, math

stage = omni.usd.get_context().get_stage()
selection = omni.usd.get_context().get_selection()

window = None
ignored_paths = set()
start_marker_path = None
end_marker_path = None
start_label = None
end_label = None
ignore_label = None

PATH_ROOT = "/World/GeneratedDronePath"
NAV_GRID_ROOT = "/World/GeneratedNavGrid"
DEBUG_ROOT = "/World/ObstacleDebugBoxes"
MARKER_ROOT = "/World/PathMarkers"

COLORS = {
    "Red": Gf.Vec3f(1, 0, 0),
    "Green": Gf.Vec3f(0, 1, 0),
    "Blue": Gf.Vec3f(0, 0, 1),
    "Yellow": Gf.Vec3f(1, 1, 0),
    "Cyan": Gf.Vec3f(0, 1, 1),
    "Purple": Gf.Vec3f(0.7, 0, 1),
    "White": Gf.Vec3f(1, 1, 1),
    "Orange": Gf.Vec3f(1, 0.45, 0),
}


# ---------------- UI HELPERS ----------------

def section(title):
    ui.Spacer(height=8)
    ui.Separator()
    ui.Label(title, height=28)


def float_input(label, default):
    with ui.HStack(height=28):
        ui.Label(label, width=170)
        f = ui.FloatField(width=130)
        f.model.set_value(default)
    return f


def string_input(label, default):
    with ui.VStack(spacing=2):
        ui.Label(label)
        f = ui.StringField(height=28)
        f.model.set_value(default)
    return f


def selected_color(combo):
    idx = combo.model.get_item_value_model().as_int
    return list(COLORS.keys())[idx]


def refresh_labels():
    if start_label:
        start_label.text = f"Start: {start_marker_path}" if start_marker_path else "Start: None"
    if end_label:
        end_label.text = f"End: {end_marker_path}" if end_marker_path else "End: None"
    if ignore_label:
        ignore_label.text = "Ignored: None" if not ignored_paths else "Ignored:\n" + "\n".join(sorted(ignored_paths))


# ---------------- USD HELPERS ----------------

def get_world_pos(prim):
    xform = UsdGeom.Xformable(prim)
    p = xform.ComputeLocalToWorldTransform(0).ExtractTranslation()
    return Gf.Vec3d(p[0], p[1], p[2])


def get_world_bbox(prim):
    cache = UsdGeom.BBoxCache(Usd.TimeCode.Default(), [UsdGeom.Tokens.default_], useExtentsHint=True)
    box = cache.ComputeWorldBound(prim).ComputeAlignedBox()
    return box.GetMin(), box.GetMax()


def clear_prim(path, label):
    if stage.GetPrimAtPath(path):
        stage.RemovePrim(path)
    print(f"Cleared {label}.")


def clear_path(): clear_prim(PATH_ROOT, "generated path")
def clear_nav_grid(): clear_prim(NAV_GRID_ROOT, "Nav Grid")
def clear_debug_boxes(): clear_prim(DEBUG_ROOT, "debug boxes")


def clear_markers():
    global start_marker_path, end_marker_path
    clear_prim(MARKER_ROOT, "markers")
    start_marker_path = None
    end_marker_path = None
    refresh_labels()


def clear_ignore():
    ignored_paths.clear()
    refresh_labels()
    print("Ignore list cleared.")


# ---------------- MARKERS ----------------

def create_marker(name, x, y, z, color):
    if not stage.GetPrimAtPath(MARKER_ROOT):
        UsdGeom.Xform.Define(stage, MARKER_ROOT)

    path = f"{MARKER_ROOT}/{name}"

    if stage.GetPrimAtPath(path):
        stage.RemovePrim(path)

    sphere = UsdGeom.Sphere.Define(stage, path)
    sphere.AddTranslateOp().Set(Gf.Vec3f(x, y, z))
    sphere.GetRadiusAttr().Set(0.45)
    sphere.GetDisplayColorAttr().Set([color])

    selection.set_selected_prim_paths([path], True)
    return path


def create_start_marker(x, y, z):
    global start_marker_path
    start_marker_path = create_marker("StartPoint", x, y, z, Gf.Vec3f(0, 1, 0))
    refresh_labels()
    print("Created Start:", start_marker_path)


def create_end_marker(x, y, z):
    global end_marker_path
    end_marker_path = create_marker("EndPoint", x, y, z, Gf.Vec3f(1, 0, 0))
    refresh_labels()
    print("Created End:", end_marker_path)


def set_selected_as_start():
    global start_marker_path
    selected = selection.get_selected_prim_paths()
    if selected:
        start_marker_path = str(selected[0])
    refresh_labels()


def set_selected_as_end():
    global end_marker_path
    selected = selection.get_selected_prim_paths()
    if selected:
        end_marker_path = str(selected[0])
    refresh_labels()


def add_selected_to_ignore():
    selected = selection.get_selected_prim_paths()
    for p in selected:
        ignored_paths.add(str(p))
    refresh_labels()


# ---------------- OBSTACLES ----------------

def is_ignored(prim):
    p = str(prim.GetPath())
    return any(p.startswith(i) for i in ignored_paths)


def is_blocked(x, y, obstacles):
    return any(min_x <= x <= max_x and min_y <= y <= max_y for min_x, max_x, min_y, max_y in obstacles)


def collect_obstacles(padding, show_debug):
    obstacles = []
    clear_debug_boxes()

    if show_debug:
        UsdGeom.Xform.Define(stage, DEBUG_ROOT)

    for prim in stage.Traverse():
        path = str(prim.GetPath())

        if path.startswith(PATH_ROOT) or path.startswith(NAV_GRID_ROOT) or path.startswith(DEBUG_ROOT) or path.startswith(MARKER_ROOT):
            continue
        if path == start_marker_path or path == end_marker_path:
            continue
        if is_ignored(prim):
            continue

        if prim.IsA(UsdGeom.Mesh) or prim.IsA(UsdGeom.Cube) or prim.IsA(UsdGeom.Capsule):
            try:
                mn, mx = get_world_bbox(prim)
                min_x, max_x = mn[0] - padding, mx[0] + padding
                min_y, max_y = mn[1] - padding, mx[1] + padding
                obstacles.append((min_x, max_x, min_y, max_y))

                if show_debug:
                    cx, cy = (min_x + max_x) * 0.5, (min_y + max_y) * 0.5
                    sx, sy = max_x - min_x, max_y - min_y
                    cube = UsdGeom.Cube.Define(stage, f"{DEBUG_ROOT}/ObstacleBox_{len(obstacles)}")
                    cube.AddTranslateOp().Set(Gf.Vec3f(cx, cy, 0.15))
                    cube.AddScaleOp().Set(Gf.Vec3f(sx * 0.5, sy * 0.5, 0.05))
                    cube.GetDisplayColorAttr().Set([Gf.Vec3f(1, 0.5, 0)])
            except Exception as e:
                print("Obstacle failed:", path, e)

    print("Total obstacles:", len(obstacles))
    return obstacles


# ---------------- PATHFINDING ----------------

def world_to_grid(x, y, min_x, min_y, step):
    return int(round((x - min_x) / step)), int(round((y - min_y) / step))


def grid_to_world(cell, min_x, min_y, step):
    return min_x + cell[0] * step, min_y + cell[1] * step


def heuristic(a, b):
    return abs(a[0] - b[0]) + abs(a[1] - b[1])


def random_astar(start, goal, min_x, max_x, min_y, max_y, step, obstacles, randomness):
    open_set = [(0, start)]
    came_from = {}
    g_score = {start: 0}

    dirs = [(1,0),(-1,0),(0,1),(0,-1),(1,1),(1,-1),(-1,1),(-1,-1)]

    while open_set:
        _, current = heapq.heappop(open_set)

        if current == goal:
            path = [current]
            while current in came_from:
                current = came_from[current]
                path.append(current)
            return list(reversed(path))

        random.shuffle(dirs)

        for dx, dy in dirs:
            nx, ny = current[0] + dx, current[1] + dy
            wx, wy = min_x + nx * step, min_y + ny * step

            if wx < min_x or wx > max_x or wy < min_y or wy > max_y:
                continue
            if is_blocked(wx, wy, obstacles):
                continue

            neighbor = (nx, ny)
            cost = 1.4 if dx and dy else 1.0
            new_g = g_score[current] + cost + random.uniform(0, randomness)

            if neighbor not in g_score or new_g < g_score[neighbor]:
                came_from[neighbor] = current
                g_score[neighbor] = new_g
                f = new_g + heuristic(neighbor, goal) + random.uniform(0, randomness)
                heapq.heappush(open_set, (f, neighbor))

    return []


def smooth_path(points, strength):
    strength = int(strength)
    if strength <= 0 or len(points) < 3:
        return points

    result = [points[0]]

    for i in range(1, len(points) - 1):
        curr, nxt = points[i], points[i + 1]
        result.append(curr)

        for s in range(strength):
            t = (s + 1) / (strength + 1)
            result.append((
                curr[0] * (1 - t) + nxt[0] * t,
                curr[1] * (1 - t) + nxt[1] * t,
                curr[2] * (1 - t) + nxt[2] * t
            ))

    result.append(points[-1])
    return result


def create_cube(path, x, y, z, size, color, height=0.04):
    cube = UsdGeom.Cube.Define(stage, path)
    cube.AddTranslateOp().Set(Gf.Vec3f(x, y, z))
    cube.AddScaleOp().Set(Gf.Vec3f(size, size, height))
    cube.GetDisplayColorAttr().Set([color])


def path_length(points):
    return sum(
        math.sqrt(
            (points[i][0] - points[i-1][0]) ** 2 +
            (points[i][1] - points[i-1][1]) ** 2 +
            (points[i][2] - points[i-1][2]) ** 2
        )
        for i in range(1, len(points))
    )


# ---------------- GENERATORS ----------------

def generate_path(area_min_x, area_max_x, area_min_y, area_max_y, spacing, padding, height, size, randomness, smooth, color_name, show_debug, save_file):
    if not start_marker_path or not end_marker_path:
        print("Set Start and End first.")
        return []

    if spacing <= 0:
        print("Spacing must be greater than 0.")
        return []

    start_prim = stage.GetPrimAtPath(start_marker_path)
    end_prim = stage.GetPrimAtPath(end_marker_path)

    if not start_prim.IsValid() or not end_prim.IsValid():
        print("Start or End marker invalid.")
        return []

    clear_path()
    obstacles = collect_obstacles(padding, show_debug)

    sp, ep = get_world_pos(start_prim), get_world_pos(end_prim)
    start = world_to_grid(sp[0], sp[1], area_min_x, area_min_y, spacing)
    goal = world_to_grid(ep[0], ep[1], area_min_x, area_min_y, spacing)

    path = random_astar(start, goal, area_min_x, area_max_x, area_min_y, area_max_y, spacing, obstacles, randomness)

    if not path:
        print("Path not created. Try regenerate, reduce padding, or increase area.")
        return []

    points = [(grid_to_world(c, area_min_x, area_min_y, spacing)[0],
               grid_to_world(c, area_min_x, area_min_y, spacing)[1],
               height) for c in path]

    points = smooth_path(points, smooth)

    color = COLORS.get(color_name, COLORS["Red"])
    UsdGeom.Xform.Define(stage, PATH_ROOT)

    for i, p in enumerate(points):
        create_cube(f"{PATH_ROOT}/PathPoint_{i}", p[0], p[1], p[2], size, color)

    print("Generated path points:", len(points))
    print("Path length:", round(path_length(points), 2))

    save_path_json(points, save_file)

    return points


def generate_nav_grid(area_min_x, area_max_x, area_min_y, area_max_y, spacing, padding, height, size, color_name, show_debug):
    clear_nav_grid()

    if spacing <= 0:
        print("Spacing must be greater than 0.")
        return

    obstacles = collect_obstacles(padding, show_debug)
    color = COLORS.get(color_name, COLORS["Green"])

    UsdGeom.Xform.Define(stage, NAV_GRID_ROOT)

    count = 0
    x = area_min_x
    while x <= area_max_x:
        y = area_min_y
        while y <= area_max_y:
            if not is_blocked(x, y, obstacles):
                create_cube(f"{NAV_GRID_ROOT}/NavCell_{count}", x, y, height, size, color, 0.025)
                count += 1
            y += spacing
        x += spacing

    print("Generated Nav Grid cells:", count)


# ---------------- JSON ----------------

def save_path_json(points, file_path):
    try:
        folder = os.path.dirname(file_path)
        if folder:
            os.makedirs(folder, exist_ok=True)

        with open(file_path, "w") as f:
            json.dump([{"x": p[0], "y": p[1], "z": p[2]} for p in points], f, indent=4)

        print("Saved path JSON:", file_path)
    except Exception as e:
        print("Failed to save JSON:", e)


def load_path_json(file_path, point_size, color_name):
    if not os.path.exists(file_path):
        print("JSON file not found:", file_path)
        return []

    with open(file_path, "r") as f:
        data = json.load(f)

    clear_path()
    UsdGeom.Xform.Define(stage, PATH_ROOT)

    color = COLORS.get(color_name, COLORS["Red"])
    points = []

    for i, p in enumerate(data):
        point = (p["x"], p["y"], p["z"])
        points.append(point)
        create_cube(f"{PATH_ROOT}/PathPoint_{i}", point[0], point[1], point[2], point_size, color)

    print("Loaded JSON path points:", len(points))
    return points


# ---------------- GUI ----------------

def create_gui():
    global window, start_label, end_label, ignore_label

    window = ui.Window("Advanced Drone Navigation Tool", width=540, height=780)

    with window.frame:
        with ui.ScrollingFrame(
            horizontal_scrollbar_policy=ui.ScrollBarPolicy.SCROLLBAR_ALWAYS_OFF,
            vertical_scrollbar_policy=ui.ScrollBarPolicy.SCROLLBAR_ALWAYS_ON,
        ):
            with ui.VStack(spacing=8, height=0):

                section("1. Area Bounds")
                area_min_x = float_input("Area Min X", -20)
                area_max_x = float_input("Area Max X", 20)
                area_min_y = float_input("Area Min Y", -20)
                area_max_y = float_input("Area Max Y", 20)

                section("2. Nav Grid Settings")
                grid_spacing = float_input("Grid Spacing", 1.0)
                grid_padding = float_input("Grid Obstacle Padding", 0.8)
                grid_height = float_input("Grid Height", 0.25)
                grid_cell_size = float_input("Grid Cell Size", 0.2)

                ui.Label("Grid Color")
                grid_color_combo = ui.ComboBox(1, *list(COLORS.keys()))

                grid_debug = ui.SimpleBoolModel(False)
                with ui.HStack(height=28):
                    ui.CheckBox(grid_debug)
                    ui.Label("Show Grid Obstacle Debug Boxes")

                with ui.HStack(height=32):
                    ui.Button("Generate Nav Grid", clicked_fn=lambda: generate_nav_grid(
                        area_min_x.model.get_value_as_float(),
                        area_max_x.model.get_value_as_float(),
                        area_min_y.model.get_value_as_float(),
                        area_max_y.model.get_value_as_float(),
                        grid_spacing.model.get_value_as_float(),
                        grid_padding.model.get_value_as_float(),
                        grid_height.model.get_value_as_float(),
                        grid_cell_size.model.get_value_as_float(),
                        list(COLORS.keys())[grid_color_combo.model.get_item_value_model().as_int],
                        grid_debug.get_value_as_bool()
                    ))

                    ui.Button("Clear Nav Grid", clicked_fn=clear_nav_grid)

                section("3. Path Settings")
                path_spacing = float_input("Path Spacing", 1.0)
                path_padding = float_input("Path Obstacle Padding", 0.8)
                path_height = float_input("Path Height", 1.5)
                path_point_size = float_input("Path Point Size", 0.25)
                path_randomness = float_input("Path Randomness", 5.0)
                path_smooth = float_input("Path Smooth Amount", 1.0)

                ui.Label("Path Color")
                path_color_combo = ui.ComboBox(0, *list(COLORS.keys()))

                path_debug = ui.SimpleBoolModel(False)
                with ui.HStack(height=28):
                    ui.CheckBox(path_debug)
                    ui.Label("Show Path Obstacle Debug Boxes")

                section("4. Start / End Markers")
                start_x = float_input("Start X", -18)
                start_y = float_input("Start Y", -18)
                start_z = float_input("Start Z", 0.5)

                end_x = float_input("End X", 18)
                end_y = float_input("End Y", 18)
                end_z = float_input("End Z", 0.5)

                with ui.HStack(height=32):
                    ui.Button("Create Start Marker", clicked_fn=lambda: create_start_marker(
                        start_x.model.get_value_as_float(),
                        start_y.model.get_value_as_float(),
                        start_z.model.get_value_as_float()
                    ))

                    ui.Button("Create End Marker", clicked_fn=lambda: create_end_marker(
                        end_x.model.get_value_as_float(),
                        end_y.model.get_value_as_float(),
                        end_z.model.get_value_as_float()
                    ))

                start_label = ui.Label("Start: None", height=40)
                end_label = ui.Label("End: None", height=40)

                with ui.HStack(height=32):
                    ui.Button("Set Selected As Start", clicked_fn=set_selected_as_start)
                    ui.Button("Set Selected As End", clicked_fn=set_selected_as_end)

                section("5. Ignore Objects")
                with ui.HStack(height=32):
                    ui.Button("Add Selected To Ignore", clicked_fn=add_selected_to_ignore)
                    ui.Button("Clear Ignore List", clicked_fn=clear_ignore)

                ignore_label = ui.Label("Ignored: None", height=120)

                section("6. Path Save / Load")
                save_path_field = string_input("Save Path JSON", "C:/temp/drone_path.json")
                load_path_field = string_input("Load Path JSON", "C:/temp/drone_path.json")

                with ui.HStack(height=32):
                    ui.Button("Generate Path", clicked_fn=lambda: generate_path(
                        area_min_x.model.get_value_as_float(),
                        area_max_x.model.get_value_as_float(),
                        area_min_y.model.get_value_as_float(),
                        area_max_y.model.get_value_as_float(),
                        path_spacing.model.get_value_as_float(),
                        path_padding.model.get_value_as_float(),
                        path_height.model.get_value_as_float(),
                        path_point_size.model.get_value_as_float(),
                        path_randomness.model.get_value_as_float(),
                        path_smooth.model.get_value_as_float(),
                        list(COLORS.keys())[path_color_combo.model.get_item_value_model().as_int],
                        path_debug.get_value_as_bool(),
                        save_path_field.model.get_value_as_string()
                    ))

                    ui.Button("Regenerate Path", clicked_fn=lambda: generate_path(
                        area_min_x.model.get_value_as_float(),
                        area_max_x.model.get_value_as_float(),
                        area_min_y.model.get_value_as_float(),
                        area_max_y.model.get_value_as_float(),
                        path_spacing.model.get_value_as_float(),
                        path_padding.model.get_value_as_float(),
                        path_height.model.get_value_as_float(),
                        path_point_size.model.get_value_as_float(),
                        path_randomness.model.get_value_as_float(),
                        path_smooth.model.get_value_as_float(),
                        list(COLORS.keys())[path_color_combo.model.get_item_value_model().as_int],
                        path_debug.get_value_as_bool(),
                        save_path_field.model.get_value_as_string()
                    ))

                with ui.HStack(height=32):
                    ui.Button("Load Path JSON", clicked_fn=lambda: load_path_json(
                        load_path_field.model.get_value_as_string(),
                        path_point_size.model.get_value_as_float(),
                        list(COLORS.keys())[path_color_combo.model.get_item_value_model().as_int]
                    ))

                    ui.Button("Clear Path", clicked_fn=clear_path)

                section("7. Clear Tools")
                with ui.HStack(height=32):
                    ui.Button("Clear Markers", clicked_fn=clear_markers)
                    ui.Button("Clear Debug Boxes", clicked_fn=clear_debug_boxes)

                ui.Spacer(height=10)
                ui.Label("Tip: Add GroundPlane and drone to Ignore List before generating.")

    refresh_labels()

create_gui()